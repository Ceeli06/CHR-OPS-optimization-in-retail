# Discrete-event simulation for a manual (human-only) retail order picking policy.
# Orders arrive via Poisson process, are batched (6-8 per cart), 
# assigned to a picker, and routed using a greedy nearest-neighbor heuristic algorithm

import heapq
import params
from dataclasses import dataclass
from orderGen import generate_orders
from setup_layout import setup_medium, map_of_coords, nearest_neighbor, path_distance, all_distance_maps
from models import Order, Picker, AMR, Batch, Metrics, SIMILARITY_BATCH_MAX_WAIT, BATCH_TIMEOUT

# Main DES simulation, where time advances only when events occur (arrivals, dispatches, completions)
class Simulation:

    def __init__(self, orders, pickers, amrs, staging=(0, 0), dist_map=None):
        self.time = 0.0
        self.event_queue = []  # Event queue containing: (time, counter, event_type, payload)
        self.event_counter = 0  # Used so events with same arrival_time process FIFO

        self.orders = sorted(orders, key=lambda o: o.arrival_time)
        self.pending_orders = []  # Orders waiting to be batched

        self.pickers = pickers
        self.amrs = amrs  # Unused in manual policy

        self.staging = staging
        self.dist_map = dist_map
        self.metrics = Metrics()

    # Push an event onto the priority queue using heapq
    def schedule(self, time: float, event_type: str, payload):
        heapq.heappush(self.event_queue, (time, self.event_counter, event_type, payload))
        self.event_counter += 1

    # Main simulation loop which processes events in chronological order until time exceeds SIM_TIME
    def run(self):
        for order in self.orders:
            self.schedule(order.arrival_time, "ORDER_ARRIVAL", order)

        self.schedule(params.SIM_TIME, "SIM_END_FLUSH", None)

        while self.event_queue:
            self.time, _, event_type, payload = heapq.heappop(self.event_queue)
            # Once SIM_TIME is exceeded, stop starting new work, but still flush
            # any in-flight PICK_COMPLETE events (their orders were already
            # removed from pending_orders, so they must still be recorded)
            if self.time > params.SIM_TIME:
                if event_type == "PICK_COMPLETE":
                    self.handle_pick_complete(payload)
                continue

            # If SIM_TIME not exceeded, handle event accordingly
            if event_type == "ORDER_ARRIVAL":
                self.handle_order_arrival(payload)
            elif event_type == "BATCH_DISPATCH":
                self.handle_batch(payload)
            elif event_type == "PICK_COMPLETE":
                self.handle_pick_complete(payload)
            elif event_type == "SIM_END_FLUSH":
                self.handle_end_flush()

        # Sum total idle times and output final metrics
        self.metrics.human_idle = sum(p.total_idle for p in self.pickers)
        self.metrics.amr_idle = sum(r.total_idle for r in self.amrs)
        self.metrics.finalize(params.SIM_TIME)

    # Add an arriving order to the queue of pending orders, dispatching a batch once enough have piled up
    def handle_order_arrival(self, order: Order):
        self.pending_orders.append(order)
        if len(self.pending_orders) >= params.BATCH_SIZE_MIN:
            self.schedule(self.time, "BATCH_DISPATCH", None)
        elif len(self.pending_orders) == 1:
            self.schedule(self.time + BATCH_TIMEOUT, "BATCH_DISPATCH", {"timeout": True})

    # At sim end, push any remaining pending orders into a final batch
    def handle_end_flush(self):
        if self.pending_orders:
            self.schedule(self.time, "BATCH_DISPATCH", {"final": True})

    # Helper that returns a set of all department names in an order
    def department_set(self, order):
        return {
            str(item.get("department", "")).lower()
            for item in order.items
        }

    # Gets Jaccard similarity of two order's department sets (0 = completely different, 1 = identical)
    def order_similarity(self, a, b):
        depts_a = self.department_set(a)
        depts_b = self.department_set(b)
        union = depts_a | depts_b
        if not union:
            return 0.0
        return len(depts_a & depts_b) / len(union)

    # Adds orders to batch starting with oldest order in queue, then any orders waiting over
    # SIMILARITY_BATCH_MAX_WAIT, then uses Jaccard helper to greedily select remaining orders
    # Returning slected and the remaining orders (in the same sequence as before)
    def select_similar_batch(self, batch_size):
        if batch_size >= len(self.pending_orders):
            return list(self.pending_orders), []

        selected_indices = {0}  # Seed = oldest order (index 0), always included
        seed = self.pending_orders[0]

        # Force-include any order that has waited too long starting with oldest
        for i in range(1, len(self.pending_orders)):
            if len(selected_indices) >= batch_size:
                break
            order = self.pending_orders[i]
            if self.time - order.arrival_time >= SIMILARITY_BATCH_MAX_WAIT:
                selected_indices.add(i)

        # Greedily fill remaining slots via. Jaccard with orders most similar to the first order (seed)
        remaining_indices = [
            i for i in range(1, len(self.pending_orders))
            if i not in selected_indices
        ]
        remaining_indices.sort(
            key=lambda i: (-self.order_similarity(seed, self.pending_orders[i]), i)
        )

        for i in remaining_indices:
            if len(selected_indices) >= batch_size:
                break
            selected_indices.add(i)

        selected_orders = [self.pending_orders[i] for i in sorted(selected_indices)]
        remaining_orders = [
            order for i, order in enumerate(self.pending_orders)
            if i not in selected_indices
        ]
        return selected_orders, remaining_orders

    # Decides batch size, then returns a batch of that size created via. select_similar_batch
    def create_batch(self, picker, force=False):
        if not self.pending_orders:
            return None
        if not force and len(self.pending_orders) < params.BATCH_SIZE_MIN:
            return None

        batch_size = min(len(self.pending_orders), params.BATCH_SIZE_MAX)
        batch_orders, self.pending_orders = self.select_similar_batch(batch_size)

        return Batch(orders=batch_orders, picker_id=picker.id, amr_id = None)

    # Greedy orrder assignment, picking whichever picker becomes available earliest
    def select_picker(self):
        return min(self.pickers, key=lambda p: p.available_time)

    # Count the total quantity of perishable item units in an order
    def perishable_item_count(self, order):
        return sum(
            item.get("quantity", 1)
            for item in order.items
            if str(item.get("department", "")).lower().find("perishable") >= 0
        )

    # Determine which of an order's coords belong to its perishable items
    # (mirrors orderGen.generate_order_coords' per-item quantity expansion)
    def perishable_coords_for_order(self, order):
        coords_iter = iter(order.coords)
        perishable_coords = set()
        for item in order.items:
            quantity = item.get("quantity", 1)
            is_perishable_item = str(item.get("department", "")).lower().find("perishable") >= 0
            for _ in range(quantity):
                coord = next(coords_iter)
                if is_perishable_item:
                    perishable_coords.add(coord)
        return perishable_coords

    # Build a nearest-neighbor route for the batch from staging through all item locations and back
    def build_route(self, orders):
        coords = []
        for order in orders:
            coords.extend(order.coords)

        unique_coords = list(dict.fromkeys(coords))
        if not unique_coords:
            return [self.staging]

        route = nearest_neighbor(unique_coords, self.dist_map, self.staging)
        if not route or route[-1] != self.staging:
            route.append(self.staging)

        return route

    # Main order handling function which routes a batch, computes pick times, and schedules its completion
    def handle_batch(self, payload):
        final = isinstance(payload, dict) and payload.get("final", False)
        # A timeout event forces a dispatch if the oldest pending order has waited BATCH_TIMEOUT
        timeout = (
            isinstance(payload, dict) and payload.get("timeout", False)
            and self.pending_orders
            and self.time - self.pending_orders[0].arrival_time >= BATCH_TIMEOUT
        )
        force = final or timeout

        # Abort if batch is not valid
        if not force and len(self.pending_orders) < params.BATCH_SIZE_MIN:
            return

        # Check picker availability before pulling orders from pending_orders,
        # so a busy picker doesn't cause orders to be lost on reschedule
        picker = self.select_picker()
        if picker.available_time > self.time:
            self.schedule(picker.available_time, "BATCH_DISPATCH", payload)
            return

        batch = self.create_batch(picker, force=force)
        if batch is None: # Invalid batch-catching
            return

        picker.mark_busy(self.time)

        route = self.build_route(batch.orders)
        travel_distance = path_distance(route, self.dist_map)
        travel_time = travel_distance / params.WALKING_SPEED 
        

        time_cursor = self.time + travel_time # Holds time from batch start to end

        # Map each location to the orders that have items there
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

        # Walk the route, picking items and updating order state at each stop
        for node in route[1:]:
            if node == self.staging:
                break
            orders_at_node = coord_orders.get(node, [])
            if not orders_at_node:
                continue

            pick_duration = len(orders_at_node) * params.HUMAN_PICK_TIME
            if pick_duration > 0:
                time_cursor += pick_duration # Update batch time every pick

            seen_orders = {}
            for order in orders_at_node: # Add all items at node to "seen orders"
                seen_orders[id(order)] = order
            for order in seen_orders.values(): # For each item in "seen orders"
                if order.pick_start_time is None:
                    order.pick_start_time = time_cursor - pick_duration
                if (order.is_perishable and order.perishable_picked_at is None
                        and node in order.perishable_coords):
                    order.perishable_picked_at = time_cursor - pick_duration
                decrement = sum(1 for coord in order.coords if coord == node)
                order.items_remaining -= decrement # Decrement items remaining in batch
                if order.items_remaining <= 0 and order.completion_time is None:
                    order.completion_time = time_cursor

        # Update walking distance of picker and global total
        picker.distance_walked += travel_distance
        self.metrics.human_distance += travel_distance

        # Update picker avalible time and schedule a pick complete event
        finish_time = time_cursor
        picker.available_time = finish_time
        self.schedule(finish_time, "PICK_COMPLETE", batch)

    # Mark picker idle, record metrics for the completed batch, and schedule the next one if ready
    def handle_pick_complete(self, batch):
        # Update picker
        picker = self.pickers[batch.picker_id]
        picker.mark_idle(self.time)

        # Record order completion times and perishible exposure times
        for order in batch.orders:
            if order.completion_time is None:
                order.completion_time = self.time
            self.metrics.record_completion(order)
            if order.is_perishable:
                if order.perishable_picked_at is not None:
                    start_time = order.perishable_picked_at
                elif order.pick_start_time is not None:
                    start_time = order.pick_start_time
                else:
                    start_time = order.arrival_time
                exposure = order.completion_time - start_time
                item_count = self.perishable_item_count(order)
                self.metrics.perishable_exposure.extend([exposure] * item_count)
                self.metrics.total_perishables += item_count
                if exposure > params.FREEZER_PERISHABLE_TIME:
                    self.metrics.spoiled_perishables += item_count

        # Schedule next batch if enough orders
        if len(self.pending_orders) >= params.BATCH_SIZE_MIN:
            self.schedule(self.time, "BATCH_DISPATCH", None)


# Main experimentation space where testing occurs
if __name__ == "__main__":
    layout = setup_medium()  # Medium layout has all 16 departments, used as baseline before layout realism changes
    coord_map = map_of_coords(layout)
    dist_map = all_distance_maps(layout)  # Precompute distances for routing
    staging = coord_map["S"][0]

    # Precompute list of orders
    raw_orders = generate_orders(coord_map, params.SIM_TIME, params.ORDER_ARRIVAL_RATE)
    orders = [
        Order(
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

    pickers = [Picker(i, staging) for i in range(params.num_pickers)]
    amrs = [AMR(i, staging) for i in range(params.num_robots)]

    sim = Simulation(orders, pickers, amrs, staging=staging, dist_map=dist_map)
    sim.run()
