'''
Defines the zone wait policy where pickers pick items within their own zone independantly,
delivering them to the handoff point when finished. AMRs travel from zone to zone waiting
at handoff points until the picker is done picking, then moving to the next zone, collecting
the batch from staging to staging.
'''

import params
import models
from orderGen import generate_orders
from setup_layout import (
    setup_layout,
    map_of_coords,
    all_distance_maps,
    path_distance,
)


# Main DES simulation, where time advances only when events occur (arrivals, dispatches, completions)
class ZoneWait(models.Simulation):
    def __init__(
        self,
        orders,
        pickers,
        amrs,
        coord_map,
        layout,
        staging=(0, 0),
        dist_map=None,
        customers=None,
    ):
        super().__init__(
            orders,
            pickers,
            amrs,
            coord_map,
            layout,
            False,
            staging=staging,
            dist_map=dist_map,
            customers=customers,
        )

        self.zoneMap = super().coordinate_zoning(layout, coord_map)
        self.handoffPoints = super().get_zone_handoff_points(self.zoneMap, layout)
    
    # Extracts pending orders into a single batch and categorizes items by zone.
    def create_batch(self, pickers, amr, force=False):
        if not self.pending_orders:
            return None
        if not force and len(self.pending_orders) < params.BATCH_SIZE_MIN:
            return None

        batch_size = min(len(self.pending_orders), params.BATCH_SIZE_MAX)
        batch_orders, self.pending_orders = super().select_similar_batch(batch_size)

        zoned_orders = super().split_orders_into_zones(batch_orders, self.zoneMap)

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

        batch = self.create_batch(self.pickers, amr, force=force)
        if batch is None:
            return

        if amr:
            amr.mark_busy(self.time)

        route = self.build_zoning_route(
            batch.zoned_orders
        )  # doesn't include start: staging and end: staging
        human_travel_distance = sum(
            path_distance(zonePath, self.dist_map) for zonePath in route.values()
        )
        self.metrics.batch_completion_count += 1

        # sets up coordinate to order # (used later in metrics determination)
        coord_orders = {}
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

        picker_finish_times = {}

        for zone_id in sorted(route.keys(), reverse=True):
            zonePath = route[zone_id]

            picker = self.pickers[zone_id]
            start_time = max(self.time, picker.available_time)
            picker_time = start_time

            picker.mark_busy(picker_time)
            prev_node = zonePath[0]

            for node in zonePath[1:-1]:
                if node == self.staging:
                    break
                walk_dist = self.dist_map[prev_node][node[0], node[1]]
                walk_time = walk_dist / (
                    min(
                        params.WALKING_SPEED * params.MANUAL_PUSH_FACTOR,
                        params.AMR_SPEED,
                    )
                )
                picker_time += walk_time

                orders_at_node = coord_orders.get(node, [])
                if not orders_at_node:
                    continue

                pick_duration = len(orders_at_node) * (
                    params.CART_LOAD_TIME + params.HUMAN_PICK_TIME
                )
                # picker collision with customers 
                pick_duration += params.HUMAN_ADAPTABILITY_FACTOR * self.customer_collisions(
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
            picker_time += dist_last_to_zone_center / min(
                params.WALKING_SPEED, params.AMR_SPEED
            )

            picker.available_time = picker_time
            picker_finish_times[zone_id] = picker_time
            picker.mark_idle(picker_time)

        # Deals w/ AMR wait time & metrics
        amr_wait_time = 0
        human_wait_time = 0

        if amr and route and not self.zoneFollow:
            # amr visits the furthest zone first then comes back to the zone containing perishables last
            zone_ids = sorted(route.keys(), reverse=True)
            amr_time = self.time
            active_amr = amr
            items_carried = 0
            current_amr_node = self.staging

            # visit zones sequentially
            for idx, zone_id in enumerate(zone_ids):
                zone_items = len(batch.zoned_orders[zone_id])

                # If this zone's items would overflow the active AMR's remaining
                # capacity, swap to a replacement before traveling to this zone
                if (
                    items_carried > 0
                    and items_carried + zone_items > params.CART_CAPACITY
                ):
                    return_dist, return_time, unload_time = self.amr_return_leg(
                        current_amr_node, items_carried
                    )
                    active_amr.available_time = amr_time + return_time + unload_time
                    active_amr.mark_idle(active_amr.available_time)
                    self.metrics.amr_distance += return_dist

                    candidates = [a for a in self.amrs if a is not active_amr]
                    replacement = (
                        min(candidates, key=lambda a: a.available_time)
                        if candidates
                        else active_amr
                    )
                    swap_dist = path_distance(
                        [self.staging, self.handoffPoints[zone_id]], self.dist_map
                    )
                    swap_wait = (
                        max(0.0, replacement.available_time - amr_time)
                        + swap_dist / params.AMR_SPEED
                    )
                    amr_time += swap_wait
                    human_wait_time += swap_wait
                    self.metrics.amr_distance += swap_dist
                    self.metrics.cart_swap_count += 1

                    replacement.mark_busy(amr_time)
                    active_amr = replacement
                    items_carried = 0
                    current_amr_node = self.handoffPoints[zone_id]
                else:
                    travel_dist = path_distance(
                        [current_amr_node, self.handoffPoints[zone_id]], self.dist_map
                    )
                    amr_time += travel_dist / params.AMR_SPEED
                    self.metrics.amr_distance += travel_dist
                    current_amr_node = self.handoffPoints[zone_id]

                picker_finish = picker_finish_times[zone_id]

                if amr_time < picker_finish:
                    # AMR arrived before picker finished
                    arrival_time = amr_time
                    wait = picker_finish - arrival_time
                    amr_wait_time += wait
                    amr_time += wait
                    active_amr.mark_idle(arrival_time)
                    active_amr.mark_busy(amr_time)

                else:
                    # Picker finished before AMR arrived
                    human_wait_time += amr_time - picker_finish

                # Handoff/loading time
                amr_time += zone_items * params.CART_LOAD_TIME
                items_carried += zone_items

            # Last zone to staging
            return_dist, return_time, unload_time = self.amr_return_leg(
                current_amr_node, items_carried
            )
            amr_time += return_time + unload_time
            self.metrics.amr_distance += return_dist

            amr_finish_time = amr_time
            active_amr.available_time = amr_finish_time
            active_amr.mark_idle(amr_finish_time)

        else:
            amr_finish_time = max(picker_finish_times.values(), default=self.time)

        self.metrics.amr_wait_for_human += amr_wait_time
        self.metrics.human_distance += human_travel_distance
        self.metrics.human_wait_for_amr += human_wait_time
        finish_time = max(
            max(picker_finish_times.values(), default=self.time), amr_finish_time
        )
        for order in batch.orders:
            if order.items_remaining <= 0 and order.at_staging_time is None:
                order.at_staging_time = finish_time

        self.schedule(finish_time, "PICK_COMPLETE", batch)


# Standalone runner for testing the policy independently
if __name__ == "__main__":
    layout = setup_layout()
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
    customers = [models.Customer(i, staging) for i in range(params.num_customers)]

    sim = ZoneWait(
        orders,
        pickers,
        amrs,
        coord_map,
        layout=layout,
        staging=staging,
        dist_map=dist_map,
        customers=customers,
    )
    sim.coordinate_zoning(layout, coord_map)
    sim.run()
