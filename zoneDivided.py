# Zone-divided policy DES where pickers stay permanently assigned to one zone each and batches 
# are split across zones similar to in zone-based wait-based, but each zone's items are carried
# to staging by an individual AMR instead of one AMR visiting every zone in sequence. AMRs act like a
# shared pool and can only be assigned to one zone at a time, so a picker in a zone may have to wait
# for an AMR to free up. An order isn't complete until every zone its items touched has reached staging.

import params
import models
from orderGen import generate_orders, convert_to_walkable
from setup_layout import (
    setup_medium,
    map_of_coords,
    get_path,
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
            orders, pickers, amrs, coord_map, layout, zoneFollow=False, staging=staging, dist_map=dist_map
        )

        self.zoneMap = self.coordinate_zoning(layout, coord_map)
        self.handoffPoints = self.get_zone_handoff_points(self.zoneMap, layout)

    # returns a zone map where zone[r][c] gives zone # (also picker_id) of location (r,c)
    def coordinate_zoning(self, layout, coord_map):
        rows = len(layout)
        cols = len(layout[0])

        numPickers = len(self.pickers)
        freezer_coords = coord_map["2"]
        walkable_freezer_coords = []
        for coord in freezer_coords:
            coord = convert_to_walkable(coord, layout)
            walkable_freezer_coords.append(coord)

        zone = [[None for _ in range(cols)] for _ in range(rows)]

        # freezer zone always assigned to last associate
        for r, c in walkable_freezer_coords:
            zone[r][c] = 0

        # splits remaining space by x-coordinate
        zone_width = cols / numPickers if numPickers else cols

        for r in range(rows):
            for c in range(cols):

                if (r, c) in walkable_freezer_coords:
                    continue

                zone_id = int(c / zone_width)
                zone_id = min(zone_id, numPickers - 1)

                zone[r][c] = zone_id
        return zone

    # returns a dict mapping of zone id to zone handoff point (center of zone)
    def get_zone_handoff_points(self, zone_map, layout):
        zone_cells = defaultdict(list)

        for r in range(len(zone_map)):
            for c in range(len(zone_map[0])):
                zone_id = zone_map[r][c]
                if zone_id is None:
                    continue
                zone_cells[zone_id].append((r, c))

        handoff_points = {}

        for zone_id, cells in zone_cells.items():
            avg_r = sum(r for r, c in cells) / len(cells)
            avg_c = sum(c for r, c in cells) / len(cells)
            coord = (math.floor(avg_r), math.floor(avg_c))
            coord = convert_to_walkable(coord, layout)
            handoff_points[zone_id] = coord
        return handoff_points

    # takes in a list of order coordinates (for one batch) and returns a list
    # of the orders to which picker/zone they are assigned to (the index)
    def split_orders_into_zones(self, orders, zone_map):
        order_zones = defaultdict(list)

        for order in orders:
            for coord in order.coords:
                r, c = coord
                zone_id = zone_map[r][c]
                order_zones[zone_id].append(coord)
        return order_zones

    def create_batch(self, pickers, amr, force=False):
        if not self.pending_orders:
            return None
        if not force and len(self.pending_orders) < params.BATCH_SIZE_MIN:
            return None

        batch_size = min(len(self.pending_orders), params.BATCH_SIZE_MAX)
        batch_orders, self.pending_orders = self.select_similar_batch(batch_size)

        zoned_orders = self.split_orders_into_zones(batch_orders, self.zoneMap)

        amrId = None
        if amr:
            amrId = amr.id
        return models.Batch(orders=batch_orders, zoned_orders=zoned_orders, amr_id=amrId)

    # Greedy order assignment, picking whichever picker becomes available earliest
    def select_picker(self):
        return min(self.pickers, key=lambda p: p.available_time)

    def select_amr(self):
        if len(self.amrs) == 0:
            return
        return min(self.amrs, key=lambda p: p.available_time)

    # Build a nearest-neighbor route for the batch from staging through all item locations and back
    def build_route(self, orders, startEnd):

        unique_coords = list(dict.fromkeys(orders))
        if not unique_coords:
            return [startEnd]

        route = get_path(unique_coords, self.dist_map, startEnd, self.map)
        if not route or route[-1] != startEnd:
            route.append(startEnd)

        return route

    def build_zoning_route(self, zoned_orders):
        route = {}

        for zone_id, coords in zoned_orders.items():
            route[zone_id] = self.build_route(coords, self.handoffPoints[zone_id])

        return route

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

        amr = self.select_amr()

        # amr availability check
        if amr and amr.available_time > self.time:
            self.schedule(amr.available_time, "BATCH_DISPATCH", payload)
            return
        # first picker check
        if self.pickers[0].available_time > self.time:
            self.schedule(self.pickers[0].available_time, "BATCH_DISPATCH", payload)
            return

        batch = self.create_batch(self.pickers, None, force=force)
        if batch is None:
            return

        route = self.build_zoning_route(batch.zoned_orders)

        human_travel_distance = sum(path_distance(zonePath, self.dist_map) for zonePath in route.values())

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
            prev_node = zonePath[0]

            for node in zonePath[1:-1]:
                if node == self.staging:
                    break
                walk_dist = self.dist_map[prev_node][node[0], node[1]]
                walk_time = walk_dist / (params.WALKING_SPEED)
                picker_time += walk_time

                orders_at_node = coord_orders.get(node, [])
                if not orders_at_node:
                    continue

                if self.zoneFollow:
                    pick_duration = len(orders_at_node) * params.AMR_LOAD_TIME
                else:
                    pick_duration = len(orders_at_node) * (params.HUMAN_PICK_TIME + params.AMR_LOAD_TIME)  # accounts for moving items to AMR @ end

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
            picker.available_time = picker_time
            picker_finish_times[zone_id] = picker_time
            picker.mark_idle(picker_time)

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
                num_trips = max(1, math.ceil(zone_item_count / params.AMR_CAPACITY))
                # Split items as evenly as possible across the trips needed to stay under capacity
                base, extra = divmod(zone_item_count, num_trips)
                trip_sizes = [base + (1 if i < extra else 0) for i in range(num_trips)]

                last_delivery_time = picker_finish
                for trip_idx, trip_items in enumerate(trip_sizes):
                    amr_id = min(tentative_available, key=tentative_available.get)
                    zone_amr = amr_by_id[amr_id]

                    departure = max(tentative_available[amr_id], self.time)
                    zone_amr.mark_busy(departure)

                    to_zone_dist = path_distance(
                        [self.staging, self.handoffPoints[zone_id]], self.dist_map
                    )
                    arrival = departure + to_zone_dist / params.AMR_SPEED

                    # Only the first trip needs to wait on the picker (further trips just need
                    # an AMR available, since the picker already dropped off all the items)
                    ready_time = picker_finish if trip_idx == 0 else last_delivery_time

                    if arrival < ready_time:
                        amr_wait_time += ready_time - arrival
                        zone_amr.mark_idle(arrival)
                        zone_amr.mark_busy(ready_time)
                    else:
                        human_wait_time += arrival - ready_time

                    pickup_time = max(arrival, ready_time)
                    loaded_time = pickup_time + params.AMR_LOAD_TIME

                    back_dist = path_distance(
                        [self.handoffPoints[zone_id], self.staging], self.dist_map
                    )
                    unload_time = params.AMR_UNLOAD_TIME * trip_items
                    delivery_time = loaded_time + back_dist / params.AMR_SPEED + unload_time

                    zone_amr.mark_idle(delivery_time)
                    zone_amr.available_time = delivery_time
                    tentative_available[amr_id] = delivery_time

                    last_delivery_time = delivery_time
                    amr_distance_total += to_zone_dist + back_dist
                    if num_trips > 1:
                        self.metrics.amr_hot_swaps += 1

                zone_delivery_time[zone_id] = last_delivery_time
        else:
            for zone_id in route.keys():
                zone_delivery_time[zone_id] = picker_finish_times[zone_id]

        self.metrics.amr_wait_for_human += amr_wait_time
        self.metrics.human_distance += human_travel_distance
        self.metrics.amr_distance += amr_distance_total
        self.metrics.human_wait_for_amr += human_wait_time
        self.metrics.human_idle += self.metrics.human_wait_for_amr

        # An order isn't complete until every zone it touched has delivered its portion
        for order in batch.orders:
            if order.items_remaining <= 0 and order.completion_time is None:
                zones = order_zones.get(id(order))
                if zones:
                    order.completion_time = max(zone_delivery_time[z] for z in zones)

        finish_time = max(zone_delivery_time.values(), default=self.time)
        self.schedule(finish_time, "PICK_COMPLETE", batch)


# Main experimentation space where testing occurs
if __name__ == "__main__":
    layout = (
        setup_medium()
    )  # Medium layout has all 16 departments, used as baseline before layout realism changes
    coord_map = map_of_coords(layout)
    dist_map = all_distance_maps(layout)  # Precompute distances for routing
    staging = coord_map["S"][0]

    # Precompute list of orders
    raw_orders = generate_orders(
        coord_map, params.SIM_TIME, layout, params.ORDER_ARRIVAL_RATE
    )
    orders = [
        models.Order(
            id=raw_order["order_id"],
            arrival_time=raw_order["arrival_time"],
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
        orders, pickers, amrs, coord_map, layout, staging=staging, dist_map=dist_map
    )
    sim.run()
