# Zone-divided policy DES where pickers stay permanently assigned to one zone each and batches
# are split across zones similar to in zone-based wait-based, but each zone's items are carried
# to staging by an individual AMR instead of one AMR visiting every zone in sequence. AMRs act like a
# shared pool and can only be assigned to one zone at a time, so a picker in a zone may have to wait
# for an AMR to free up. An order isn't complete until every zone its items touched has reached staging.

import params
import models
from orderGen import generate_orders, convert_to_walkable
from setup_layout import (
    setup_layout,
    map_of_coords,
    all_distance_maps,
    path_distance,
)
from collections import defaultdict
import math


class ZoneDivided(models.Simulation):
    def __init__(
        self, orders, pickers, amrs, coord_map, layout, staging=(0, 0), dist_map=None
    ):
        super().__init__(
            orders,
            pickers,
            amrs,
            coord_map,
            layout,
            zoneFollow=False,
            staging=staging,
            dist_map=dist_map,
        )

        self.zoneMap = super().coordinate_zoning(layout, coord_map)
        self.handoffPoints = super().get_zone_handoff_points(self.zoneMap, layout)

    def create_batch(self, pickers, amr, force=False):
        if not self.pending_orders:
            return None
        if not force and len(self.pending_orders) < params.BATCH_SIZE_MIN:
            return None

        batch_size = min(len(self.pending_orders), params.BATCH_SIZE_MAX)
        batch_orders, self.pending_orders = super().select_similar_batch(batch_size)

        zoned_orders = self.split_orders_into_zones(batch_orders, self.zoneMap)

        amrId = None
        if amr:
            amrId = amr.id
        return models.Batch(
            orders=batch_orders, zoned_orders=zoned_orders, amr_id=amrId
        )

    # Main order handling function which routes a batch, computes pick times, and schedules its completion
    def handle_batch(self, payload):
        final = isinstance(payload, dict) and payload.get("final", False)

        timeout = (
            isinstance(payload, dict)
            and payload.get("timeout", False)
            and self.pending_orders
            and self.time - self.pending_orders[0].arrival_time >= params.BATCH_TIMEOUT
        )

        force = final or timeout

        if not force and len(self.pending_orders) < params.BATCH_SIZE_MIN:
            return

        amr = super().select_amr()

        # amr availability check
        if amr and amr.available_time > self.time:
            self.schedule(amr.available_time, "BATCH_DISPATCH", payload)
            return

        batch = self.create_batch(self.pickers, None, force=force)
        if batch is None:
            return

        if amr:
            amr.mark_busy(self.time)

        route = self.build_zoning_route(batch.zoned_orders)

        human_travel_distance = sum(
            path_distance(zonePath, self.dist_map) for zonePath in route.values()
        )

        # sets up coordinate to order # (used later in metrics determination),
        # and which zones each order has items in
        coord_orders = {}
        order_zones = defaultdict(set)
        for order in batch.orders:
            order.pick_start_time = None
            order.items_remaining = len(order.coords)

            order.is_perishable = any(
                "perishable" in str(item.get("department", "")).lower()
                for item in order.items
            )

            order.perishable_coords = self.perishable_coords_for_order(order)
            order.perishable_picked_at = None

            for coord in order.coords:
                coord_orders.setdefault(coord, []).append(order)
                r, c = coord
                order_zones[id(order)].add(self.zoneMap[r][c])

        picker_finish_times = {}

        for zone_id, zonePath in route.items():

            picker = self.pickers[zone_id]

            start_time = max(self.time, picker.available_time)
            picker_time = start_time

            picker.mark_busy(start_time)
            prev_node = zonePath[0] # Picker starts at the handoff point

            for node in zonePath[1:-1]:
                if node == self.staging:
                    break
                walk_dist = self.dist_map[prev_node][node[0], node[1]]
                walk_time = walk_dist / (params.WALKING_SPEED)
                picker_time += walk_time

                orders_at_node = coord_orders.get(node, [])
                if not orders_at_node:
                    continue
                
                pick_duration = len(orders_at_node) * (
                    params.HUMAN_PICK_TIME + params.CART_LOAD_TIME
                )  
                pick_duration += self.customer_collisions(
                        node, picker_time, picker_time + pick_duration
                )

                picker_time += pick_duration

                unique_orders = {id(o): o for o in orders_at_node}

                for order in unique_orders.values():
                    if order.pick_start_time is None:
                        order.pick_start_time = picker_time - pick_duration

                    if (
                        order.is_perishable
                        and order.perishable_picked_at is None
                        and node in order.perishable_coords
                    ):
                        order.perishable_picked_at = picker_time - pick_duration

                    decrement = sum(1 for c in order.coords if c == node)
                    order.items_remaining -= decrement

                prev_node = node
            # walking from last picking point to handoff point
            r, c = self.handoffPoints[zone_id]
            dist_last_to_zone_center = self.dist_map[prev_node][r, c]
            picker_time += dist_last_to_zone_center / params.WALKING_SPEED
            #picker.available_time = picker_time
            picker_finish_times[zone_id] = picker_time
            #picker.mark_idle(picker_time)
        # times when the order is at the handoff point
        zone_delivery_time = {}
        amr_wait_time = 0.0
        human_wait_time = 0.0
        amr_distance_total = 0.0

        if self.amrs:
            tentative_available = {a.id: a.available_time for a in self.amrs}
            amr_by_id = {a.id: a for a in self.amrs}

            # Whichever zone's picker finishes first claims an AMR first
            for zone_id in sorted(route.keys(), key=lambda z: picker_finish_times[z]):
                picker_finish = picker_finish_times[zone_id]
                zone_item_count = len(batch.zoned_orders[zone_id])
                num_trips = max(1, math.ceil(zone_item_count / params.AMR_AND_CART_CAPACITY))

                # Split items as evenly as possible across the trips needed to stay under capacity
                base, extra = divmod(zone_item_count, num_trips)
                trip_sizes = [base + (1 if i < extra else 0) for i in range(num_trips)]

                last_delivery_time = picker_finish
                last_pickup_time = picker_finish
                for trip_idx, trip_items in enumerate(trip_sizes):
                    amr_id = min(tentative_available, key=tentative_available.get)
                    zone_amr = amr_by_id[amr_id]
                    

                    departure = max(tentative_available[amr_id], self.time)
                    zone_amr.mark_busy(departure)

                    to_zone_dist = path_distance(
                        [self.staging, self.handoffPoints[zone_id]], self.dist_map
                    )
                    arrival = departure + to_zone_dist / params.AMR_SPEED

                    # First trip needs picker to be there, further trips know that picker is alr there so
                    # uses last_delivery_time
                    ready_time = picker_finish if trip_idx == 0 else last_delivery_time

                    if arrival < ready_time:
                        amr_wait_time += ready_time - arrival
                        #zone_amr.mark_idle(arrival)
                        #zone_amr.mark_busy(ready_time)
                    else:
                        human_wait_time += arrival - ready_time

                    pickup_time = max(arrival, ready_time)
                    
                    loaded_time = pickup_time + params.CART_LOAD_TIME * trip_items
                    last_pickup_time = loaded_time

                    back_dist = path_distance(
                        [self.handoffPoints[zone_id], self.staging], self.dist_map
                    )
                    unload_time = params.AMR_AND_CART_UNLOAD_TIME * trip_items
                    delivery_time = (
                        loaded_time + back_dist / params.AMR_SPEED + unload_time
                    )

                    zone_amr.mark_idle(delivery_time)
                    zone_amr.available_time = delivery_time
                    tentative_available[amr_id] = delivery_time

                    last_delivery_time = delivery_time
                    amr_distance_total += to_zone_dist + back_dist
                    if num_trips > 1:
                        self.metrics.amr_swap_count += 1

                zone_delivery_time[zone_id] = last_delivery_time
                # Picker is occupied until the AMR finishes taking ALL items from the Zone's batch
                picker = self.pickers[zone_id]
                free_time = last_pickup_time 
                picker.available_time = free_time
                picker.mark_idle(free_time)
                
        else:
            for zone_id in route.keys():
                zone_delivery_time[zone_id] = picker_finish_times[zone_id]
                picker = self.pickers[zone_id]
                picker.available_time = picker_finish_times[zone_id]
                picker.mark_idle(picker_finish_times[zone_id])

        self.metrics.amr_wait_for_human += amr_wait_time
        self.metrics.human_distance += human_travel_distance
        self.metrics.amr_distance += amr_distance_total
        self.metrics.human_wait_for_amr += human_wait_time
        #self.metrics.human_idle += human_wait_time
        # An order isn't complete until every zone it touched has delivered its portion
        for order in batch.orders:
            if order.items_remaining <= 0 and order.at_staging_time is None:
                zones = order_zones.get(id(order))
                if zones:
                    order.at_staging_time = max(zone_delivery_time[z] for z in zones)

        finish_time = max(zone_delivery_time.values(), default=self.time)
        self.metrics.batch_completion_count += 1
        self.schedule(finish_time, "PICK_COMPLETE", batch)


# Main experimentation space where testing occurs
if __name__ == "__main__":
    layout = (
        setup_layout()
    )  # Medium layout has all 16 departments, used as baseline before layout realism changes
    coord_map = map_of_coords(layout)
    dist_map = all_distance_maps(layout)  # Precompute distances for routing
    staging = coord_map["S"][0]

    # Precompute list of orders
    raw_orders = generate_orders(
        coord_map, params.SIM_TIME, layout, params.order_arrival_rate
    )
    
    orders = [
        models.Order(
            id=raw_order["order_id"],
            arrival_time=raw_order["arrival_time"],
            due_time=raw_order["due_time"],
            items=raw_order["items"],
            coords=raw_order["coords"],
            is_perishable=any(
                str(item.get("department", "")).lower().find("perishable") >= 0
                for item in raw_order["items"]
            ),
        )
        for raw_order in raw_orders
    ]

    pickers = [models.Picker(i, staging) for i in range(params.num_pickers)]
    amrs = [models.AMR(i, staging) for i in range(params.num_robots)]

    sim = ZoneDivided(
        orders, pickers, amrs, coord_map, layout=layout, staging=staging, dist_map=dist_map
    )
    sim.run()
