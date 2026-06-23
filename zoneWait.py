import params
import models
from orderGen import generate_orders, convert_to_walkable
from setup_layout import (
    setup_layout,
    map_of_coords,
    get_path,
    all_distance_maps,
    path_distance,
)
from collections import defaultdict
import math


# Main DES simulation, where time advances only when events occur (arrivals, dispatches, completions)
class ZoneWait(models.Simulation):
    def __init__(
        self, orders, pickers, amrs, coord_map, layout, staging=(0, 0), dist_map=None
    ):
        super().__init__(
            orders, pickers, amrs, coord_map, layout, False, staging=staging, dist_map=dist_map
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
        return models.Batch(orders=batch_orders, zoned_orders = zoned_orders, amr_id=amrId)

    # Greedy order assignment, picking whichever picker becomes available earliest

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
        
       

        batch = self.create_batch(self.pickers, amr, force=force)
        if batch is None:
            return

        if amr:
            amr.mark_busy(self.time)

        route = self.build_zoning_route(batch.zoned_orders) #doesn't include start: staging and end: staging
        human_travel_distance = sum(path_distance(zonePath, self.dist_map) for zonePath in route.values())
        self.metrics.batch_completion_count+=1
        


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
                walk_time = walk_dist / (min(params.WALKING_SPEED, params.AMR_SPEED))
                picker_time += walk_time
                
                
                orders_at_node = coord_orders.get(node, [])
                if not orders_at_node:
                    continue

                if self.zoneFollow:
                    pick_duration = len(orders_at_node) * params.AMR_LOAD_TIME
                else:
                    pick_duration = len(orders_at_node) * (params.HUMAN_PICK_TIME + params.AMR_LOAD_TIME) # accounts for moving items to AMR @ end

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
            
            picker.available_time = picker_time
            picker_finish_times[zone_id]= picker_time
            picker.mark_idle(picker_time)

        # Deals w/ AMR wait time & metrics
        amr_wait_time = 0
        human_wait_time = 0

        if amr and route and not self.zoneFollow:
            #amr visits the furthest zone first then comes back to the zone containing perishables last
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
                if items_carried > 0 and items_carried + zone_items > params.AMR_CAPACITY:
                    return_dist, return_time, unload_time = self.amr_return_leg(current_amr_node, items_carried)
                    active_amr.available_time = amr_time + return_time + unload_time
                    active_amr.mark_idle(active_amr.available_time)
                    self.metrics.amr_distance += return_dist

                    candidates = [a for a in self.amrs if a is not active_amr]
                    replacement = min(candidates, key=lambda a: a.available_time) if candidates else active_amr
                    swap_dist = path_distance([self.staging, self.handoffPoints[zone_id]], self.dist_map)
                    swap_wait = max(0.0, replacement.available_time - amr_time) + swap_dist / params.AMR_SPEED
                    amr_time += swap_wait
                    human_wait_time += swap_wait
                    self.metrics.amr_distance += swap_dist
                    self.metrics.amr_hot_swaps += 1

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
                amr_time += params.AMR_LOAD_TIME
                items_carried += zone_items

            # Last zone to staging
            return_dist, return_time, unload_time = self.amr_return_leg(current_amr_node, items_carried)
            amr_time += return_time + unload_time
            self.metrics.amr_distance += return_dist

            amr_finish_time = amr_time
            active_amr.available_time = amr_finish_time
            active_amr.mark_idle(amr_finish_time)

        else:
            # zone-follow mode or no AMR
            amr_finish_time = max(
                picker_finish_times.values(),
                default=self.time
            )

        self.metrics.amr_wait_for_human += amr_wait_time
        self.metrics.human_distance += human_travel_distance
        self.metrics.human_wait_for_amr += human_wait_time
        self.metrics.human_idle += human_wait_time
        finish_time = max(
            max(picker_finish_times.values(), default=self.time),
            amr_finish_time
        )
        for order in batch.orders:
            if order.items_remaining <= 0 and order.completion_time is None:
                order.completion_time = finish_time

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

    sim = ZoneWait(
        orders, pickers, amrs, coord_map, layout, staging=staging, dist_map=dist_map
    )
    sim.coordinate_zoning(layout, coord_map)
    sim.run()
