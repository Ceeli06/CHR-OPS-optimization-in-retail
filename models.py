# Discrete-event simulation for a manual (human-only) retail order picking policy.
# Orders arrive via Poisson process, are batched (6-8 per cart), 
# assigned to a picker, and routed using a greedy nearest-neighbor heuristic algorithm

import params
import heapq
from dataclasses import dataclass

# A customer order with items to be picked from the store
@dataclass
class Order:
    id: int
    arrival_time: float
    items: list  # List of dicts with "department" and "quantity" keys
    coords: list  # (row, col) locations of each item in the store
    is_perishable: bool = False

    pick_start_time: float = None  # When picker first touches this order's items
    completion_time: float = None  # When all items are picked

    due_time: float = 0.0
    perishable_coords: set = None  # Coords belonging to this order's perishable items
    perishable_picked_at: float = None  # When a perishable item was picked


# A human store associate who picks orders
@dataclass
class Picker:
    id: int
    location: tuple
    available_time: float = 0.0  # When this picker becomes free for the next batch
    is_idle: bool = True
    idle_start: float = 0.0
    total_idle: float = 0.0
    distance_walked: float = 0.0

    # Mark picker as busy and add the idle time to total_idle time
    def mark_busy(self, current_time: float):
        if self.is_idle:
            self.total_idle += current_time - self.idle_start
            self.is_idle = False

    # Mark picker as idle and record when it became idle
    def mark_idle(self, current_time: float):
        if not self.is_idle:
            self.is_idle = True
            self.idle_start = current_time


# An Autonomous Mobile Robot (currently unused in the manual policy, just here for use later)
@dataclass
class AMR:
    id: int
    location: tuple
    available_time: float = 0.0
    is_idle: bool = True
    idle_start: float = 0.0
    total_idle: float = 0.0

    def mark_busy(self, current_time: float):
        if self.is_idle:
            self.total_idle += current_time - self.idle_start
            self.is_idle = False

    # Mark AMR as idle and record when it became idle
    def mark_idle(self, current_time: float):
        if not self.is_idle:
            self.is_idle = True
            self.idle_start = current_time


# A set of orders grouped together for one picker to handle (6-8 per cart for manual)
@dataclass
class Batch:
    orders: list
    picker_id: int
    amr_id: int


# Utilities to collect and print key performance metrics at end of simulation
class Metrics:
    def __init__(self):
        self.completion_times = []  # List of order completion times (comp.Time = comp.time - arrivalTime)
        self.late_orders = 0
        self.total_orders = 0

        self.human_distance = 0.0
        self.amr_distance = 0.0

        self.human_idle = 0.0
        self.amr_idle = 0.0

        self.perishable_exposure = []
        self.spoiled_perishables = 0
        self.total_perishables = 0
        self.human_wait_for_amr = 0.0

    # Record a completed order's comp.time and check if it missed the due time
    def record_completion(self, order: Order):
        final_completion_time = (order.completion_time - order.arrival_time) + params.STAGING_TIME
        self.completion_times.append(final_completion_time)

        if final_completion_time > params.ORDER_DUE_TIME:
            self.late_orders += 1

        self.total_orders += 1

    # Compute and print all final metrics
    def finalize(self, sim_time: float):
        avg_completion = sum(self.completion_times) / len(self.completion_times) if self.completion_times else 0.0
        late_pct = (self.late_orders / self.total_orders) * 100 if self.total_orders else 0.0
        throughput = self.total_orders / (sim_time / 3600) if sim_time > 0 else 0.0

        # AMR utilization (ignored for manual policy since AMR not used)
        amr_util = ((sim_time - ((self.amr_idle)/params.num_robots)) / sim_time) * 100 if sim_time > 0 else 0.0
        # NOTE: above breaks down when there are just amrs idle (never utilized), will be negative..is this okay?
        

        avg_exposure = (
            sum(self.perishable_exposure) / len(self.perishable_exposure)
            if self.perishable_exposure else 0.0
        )

        spoiled_pct = (
            (self.spoiled_perishables / self.total_perishables) * 100
            if self.total_perishables else 0.0
        )

        print("\n===== METRICS =====")
        print(f"Avg completion time: {avg_completion/60:.2f} min")
        print(f"Late orders: {late_pct:.2f}%")
        print(f"Total picker travel distance: {self.human_distance:.2f} meters")
        print(f"Total picker idle time: {self.human_idle/60:.2f} min")
        print(f"Human wait time for AMR: {self.human_wait_for_amr/60:.2f} min")
        print(f"Total AMR idle time: {self.amr_idle/60:.2f} min")
        print(f"Average AMR utilization: {amr_util:.2f}%")
        print(f"Avg perishable exposure time: {avg_exposure/60:.2f} min")
        print(f"Spoiled perishables: {spoiled_pct:.2f}%")
        print(f"Throughput: {throughput:.2f} orders/hour")


# Parent simulation class that policy-specific simulations inherit from
@dataclass
class Simulation:
    def __init__(self, orders, pickers, amrs, coord_map, staging=(0, 0), dist_map=None):
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
        self.map = coord_map

        #Sets up event queue for scheduling order events
        for order in self.orders:
            self.schedule(order.arrival_time, "ORDER_ARRIVAL", order)

        self.schedule(params.SIM_TIME, "SIM_END_FLUSH", None)

    # Push an event onto the priority queue using heapq
    def schedule(self, time: float, event_type: str, payload):
        heapq.heappush(self.event_queue, (time, self.event_counter, event_type, payload))
        self.event_counter += 1

    # Main simulation loop which processes events in chronological order until time exceeds SIM_TIME
    def run(self):
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
    # Sends order to be batched
    def handle_order_arrival(self, order: Order):
        self.pending_orders.append(order)
        if len(self.pending_orders) >= params.BATCH_SIZE_MIN:
            self.schedule(self.time, "BATCH_DISPATCH", None)
        elif len(self.pending_orders) == 1:
            self.schedule(self.time + params.BATCH_TIMEOUT, "BATCH_DISPATCH", {"timeout": True})

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
            if self.time - order.arrival_time >= params.SIMILARITY_BATCH_MAX_WAIT:
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

    # Mark picker idle, record metrics for the completed batch, and schedule the next one if ready
    def handle_pick_complete(self, batch):
        # Update picker
        picker = self.pickers[batch.picker_id]
        picker.mark_idle(self.time)
        if (batch.amr_id):
            amr = self.amrs[batch.amr_id]
            amr.mark_idle(self.time)

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
