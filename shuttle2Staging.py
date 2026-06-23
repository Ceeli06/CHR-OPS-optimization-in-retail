# Shuttle-to-staging policy DES where pickers are dispatched dynamically based on whoever has the least
# total busy time to whichever zone has a ready batch, meeting a follower AMR at the first item of the batch.
# Then, they pick everything in that batch and the picker is set to idle the instant picking ends.
# The AMR then shuttles the finished batch back to staging on its own, desynchronized from the picker.

import math
import params
import models
from collections import defaultdict
from orderGen import generate_orders, convert_to_walkable
from setup_layout import setup_layout, map_of_coords, get_path, path_distance, all_distance_maps
class ShuttleSim(models.Simulation):
    num_zones = params.num_pickers

    def __init__(self, orders, pickers, amrs, coord_map, layout, staging=(0, 0), dist_map=None, customers=None):
        super().__init__(orders, pickers, amrs, coord_map, staging=staging, dist_map=dist_map, customers=customers)
        self.layout = layout
    # NOTE: would move this into initiate so you always know on run that it exists rather than checking everytime
    def ensure_zone_state(self):
        if hasattr(self, "zone_grid"):
            return
        self.zone_grid = self.build_zone_grid()
        self.handoff_points = self.build_handoff_points()
        self.zone_pending = {z: [] for z in range(self.num_zones)}
        self.picker_busy_time = {p.id: 0.0 for p in self.pickers}
    # NOTE: may want to move this into models because the other zone policies also use it 
    def build_zone_grid(self):
        rows = len(self.layout)
        cols = len(self.layout[0])

        freezer_coords = self.map.get("2", [])
        walkable_freezer_coords = set()
        for coord in freezer_coords:
            walkable_freezer_coords.add(convert_to_walkable(coord, self.layout))

        zone = [[None for _ in range(cols)] for _ in range(rows)]

        for r, c in walkable_freezer_coords:
            zone[r][c] = 0

        zone_width = cols / self.num_zones
        for r in range(rows):
            for c in range(cols):
                if (r, c) in walkable_freezer_coords:
                    continue
                zone_id = min(int(c / zone_width), self.num_zones - 1)
                zone[r][c] = zone_id

        return zone

    # NOTE: may want to move this into models because the other zone policies also use it 
    def build_handoff_points(self):
        zone_cells = defaultdict(list)
        for r in range(len(self.zone_grid)):
            for c in range(len(self.zone_grid[0])):
                zone_id = self.zone_grid[r][c]
                if zone_id is None:
                    continue
                zone_cells[zone_id].append((r, c))

        handoff_points = {}
        for zone_id, cells in zone_cells.items():
            avg_r = sum(r for r, c in cells) / len(cells)
            avg_c = sum(c for r, c in cells) / len(cells)
            coord = (math.floor(avg_r), math.floor(avg_c))
            handoff_points[zone_id] = convert_to_walkable(coord, self.layout)

        return handoff_points

    # Assignes a zone to an order based on which zone contains the majority of its items
    def zone_for_order(self, order):
        counts = {}
        for (r, c) in order.coords:
            zone_id = self.zone_grid[r][c]
            counts[zone_id] = counts.get(zone_id, 0) + 1
        return max(counts, key=lambda z: counts[z])

    def handle_order_arrival(self, order):
        self.ensure_zone_state()
        zone = self.zone_for_order(order)
        order.zone = zone
        queue = self.zone_pending[zone]
        queue.append(order)
        if len(queue) >= params.BATCH_SIZE_MIN:
            self.schedule(self.time, "BATCH_DISPATCH", {"zone": zone})
        elif len(queue) == 1:
            self.schedule(
                self.time + params.BATCH_TIMEOUT, "BATCH_DISPATCH", {"zone": zone, "timeout": True}
            )

    def handle_end_flush(self):
        self.ensure_zone_state()
        for zone in range(self.num_zones):
            if self.zone_pending[zone]:
                self.schedule(self.time, "BATCH_DISPATCH", {"zone": zone, "final": True})

    def create_batch(self, zone, picker, amr, force=False):
        queue = self.zone_pending[zone]
        if not queue:
            return None
        if not force and len(queue) < params.BATCH_SIZE_MIN:
            return None

        batch_size = min(len(queue), params.BATCH_SIZE_MAX)
        saved_pending = self.pending_orders
        self.pending_orders = queue
        try:
            batch_orders, remaining = self.select_similar_batch(batch_size)
        finally:
            self.pending_orders = saved_pending
        self.zone_pending[zone] = remaining

        amr_id = None
        if amr:
            amr_id = amr.id

        return models.Batch(orders=batch_orders, picker_id=picker.id, amr_id=amr_id)

    # Least-busiest picker (picker with least total working time) selection
    def select_picker(self):
        self.ensure_zone_state()
        return min(self.pickers, key=lambda p: self.picker_busy_time[p.id])

    def select_amr(self):
        if len(self.amrs) == 0:
            return
        return min(self.amrs, key=lambda p: p.available_time)

    # Builds a decoupled route that is not synchronized with another resource (other AMR/Picker)
    def build_decoupled_route(self, orders, start_node):
        coords = []
        for order in orders:
            coords.extend(order.coords)

        unique_coords = list(dict.fromkeys(coords))
        if not unique_coords:
            return [start_node]

        route = get_path(unique_coords, self.dist_map, start_node, self.map)
        return route[:-1]

    # Main order handling function which routes a batch, computes pick times, and schedules its
    # completion. Implements least-loaded picker selection and desynchronized pickers/AMRs
    def handle_batch(self, payload):
        self.ensure_zone_state()
        zone = payload["zone"] if isinstance(payload, dict) else None
        if zone is None:
            return

        final = isinstance(payload, dict) and payload.get("final", False)
        queue = self.zone_pending[zone]
        timeout = (
            isinstance(payload, dict) and payload.get("timeout", False)
            and queue
            and self.time - queue[0].arrival_time >= params.BATCH_TIMEOUT
        )
        force = final or timeout

        if not force and len(queue) < params.BATCH_SIZE_MIN:
            return

        picker = self.select_picker()
        amr = self.select_amr()

        # Reschedule if the chosen (least loaded) picker or AMR isn't free yet.
        if picker.available_time > self.time or (amr is not None and amr.available_time > self.time):
            amr_time = amr.available_time if amr else 0
            self.schedule(max(picker.available_time, amr_time), "BATCH_DISPATCH", payload)
            return

        batch = self.create_batch(zone, picker, amr, force=force)
        if batch is None:
            return
        self.metrics.batch_completion_count +=1
        dispatch_time = self.time
        picker.mark_busy(self.time)
        if amr:
            amr.mark_busy(self.time)

        meeting_point = self.handoff_points[zone]

        picker_to_start_dist = path_distance([picker.location, meeting_point], self.dist_map)
        amr_to_start_dist = 0.0
        if amr:
            amr_to_start_dist = path_distance([self.staging, meeting_point], self.dist_map)

        picker_arrival = self.time + (picker_to_start_dist / params.WALKING_SPEED)
        amr_arrival = self.time
        if amr:
            amr_arrival = self.time + (amr_to_start_dist / params.AMR_SPEED)

        sync_start_time = max(picker_arrival, amr_arrival)

        human_wait = max(0.0, amr_arrival - picker_arrival)
        self.metrics.human_wait_for_amr += human_wait
        self.metrics.human_idle += human_wait

        amr_wait = max(0.0, picker_arrival - amr_arrival)
        if amr and amr_wait > 0:
            self.metrics.amr_wait_for_human += amr_wait
            amr.mark_idle(amr_arrival)
            amr.mark_busy(sync_start_time)

        route = self.build_decoupled_route(batch.orders, meeting_point)
        picking_distance = path_distance(route, self.dist_map)

        human_picking_time = picking_distance / params.WALKING_SPEED
        amr_picking_time = 0.0
        if amr:
            amr_picking_time = picking_distance / params.AMR_SPEED

        time_cursor = sync_start_time + max(human_picking_time, amr_picking_time)

        coord_orders = {}
        for order in batch.orders:
            order.pick_start_time = None
            order.items_remaining = len(order.coords)
            order.is_perishable = any(
                str(item.get("department", "")).lower().find("perishable") >= 0
                for item in order.items
            )
            order.perishable_coords = self.perishable_coords_for_order(order)
            order.perishable_picked_at = None
            for coord in order.coords:
                coord_orders.setdefault(coord, []).append(order)

        active_amr = amr
        items_carried = 0
        last_amr_node = meeting_point

        # Walk the route, picking items and updating order state at each stop
        for node in route:
            if node == self.staging:
                break
            orders_at_node = coord_orders.get(node, [])
            if not orders_at_node:
                continue
            if amr:
                pick_duration = len(orders_at_node) * params.AMR_LOAD_TIME
                pick_duration += self.customer_collisions(node, time_cursor, time_cursor + pick_duration)
            else:
                pick_duration = len(orders_at_node) * params.HUMAN_PICK_TIME
            if pick_duration > 0:
                time_cursor += pick_duration

            seen_orders = {}
            for order in orders_at_node:
                seen_orders[id(order)] = order
            for order in seen_orders.values():
                if order.pick_start_time is None:
                    order.pick_start_time = time_cursor - pick_duration
                if (order.is_perishable and order.perishable_picked_at is None
                        and node in order.perishable_coords):
                    order.perishable_picked_at = time_cursor - pick_duration
                decrement = sum(1 for coord in order.coords if coord == node)
                order.items_remaining -= decrement

            if amr:
                items_carried += sum(
                    1 for order in orders_at_node for coord in order.coords if coord == node
                )
                last_amr_node = node

                if items_carried >= params.AMR_CAPACITY:
                    # Full AMR heads back to staging to unload; doesn't block the picker
                    return_dist, return_time, unload_time = self.amr_return_leg(node, items_carried)
                    active_amr.available_time = time_cursor + return_time + unload_time
                    active_amr.mark_idle(active_amr.available_time)
                    self.metrics.amr_distance += return_dist

                    # AMR is replaced
                    candidates = [a for a in self.amrs if a is not active_amr]
                    replacement = min(candidates, key=lambda a: a.available_time) if candidates else active_amr
                    swap_dist = path_distance([self.staging, node], self.dist_map)
                    swap_wait = max(0.0, replacement.available_time - time_cursor) + swap_dist / params.AMR_SPEED
                    time_cursor += swap_wait
                    self.metrics.human_wait_for_amr += swap_wait
                    self.metrics.human_idle += swap_wait
                    self.metrics.amr_distance += swap_dist
                    self.metrics.amr_swap_count += 1

                    replacement.mark_busy(time_cursor)
                    active_amr = replacement
                    items_carried = 0

        last_item_location = meeting_point
        if (route):
            last_item_location = route[-1]

        picker.location = last_item_location # Picker ends the order at the last item location
        picker.available_time = time_cursor
        picker.mark_idle(time_cursor)
        self.picker_busy_time[picker.id] += time_cursor - dispatch_time

        total_human_distance = picker_to_start_dist + picking_distance
        picker.distance_walked += total_human_distance
        self.metrics.human_distance += total_human_distance

        if (amr):
            # AMR (whichever is currently active after any hot-swaps) ends the order at staging
            amr_return_dist, amr_return_time, unload_time = self.amr_return_leg(last_amr_node, items_carried)
            active_amr.available_time = time_cursor + amr_return_time + unload_time
            active_amr.mark_idle(active_amr.available_time)

            total_amr_distance = amr_to_start_dist + picking_distance + amr_return_dist
            self.metrics.amr_distance += total_amr_distance
            self.schedule(active_amr.available_time, "PICK_COMPLETE", batch)
        else:
            self.schedule(time_cursor, "PICK_COMPLETE", batch)


# Main experimentation space where testing occurs
if __name__ == "__main__":
    layout = setup_layout()  
    coord_map = map_of_coords(layout)
    dist_map = all_distance_maps(layout)  # Precompute distances for routing
    staging = coord_map["S"][0]

    # Precompute list of orders
    raw_orders = generate_orders(coord_map, params.SIM_TIME, layout, params.order_arrival_rate)
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
    customers = [models.Customer(i, staging) for i in range(params.num_customers)]

    sim = ShuttleSim(orders, pickers, amrs, coord_map, layout, staging=staging, dist_map=dist_map, customers=customers)
    sim.run()
