# Discrete-event simulation for a manual (human-only) retail order picking policy.
# Orders arrive via Poisson process, are batched (6-8 per cart), 
# assigned to a picker, and routed using a greedy nearest-neighbor heuristic algorithm

import params
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


# An Autonomous Mobile Robot 
@dataclass
class AMR:
    id: int
    location: tuple
    available_time: float = 0.0
    is_idle: bool = True
    idle_start: float = 0.0
    total_idle: float = 0.0


# A set of orders grouped together for one picker to handle (6-8 per cart for manual)
@dataclass
class Batch:
    orders: list
    picker_id: int


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

    # Record a completed order's comp.time and check if it missed the due time
    def record_completion(self, order: Order):
        completion_delay = order.completion_time - order.arrival_time
        self.completion_times.append(completion_delay)

        if completion_delay > params.ORDER_DUE_TIME:
            self.late_orders += 1

        self.total_orders += 1

    # Compute and print all final metrics
    def finalize(self, sim_time: float):
        avg_completion = sum(self.completion_times) / len(self.completion_times) if self.completion_times else 0.0
        late_pct = (self.late_orders / self.total_orders) * 100 if self.total_orders else 0.0
        throughput = self.total_orders / (sim_time / 3600) if sim_time > 0 else 0.0

        # AMR utilization (ignored for manual policy since AMR not used)
        amr_util = ((sim_time - self.amr_idle) / sim_time) * 100 if sim_time > 0 else 0.0

        avg_exposure = (
            sum(self.perishable_exposure) / len(self.perishable_exposure)
            if self.perishable_exposure else 0.0
        )

        print("\n===== METRICS =====")
        print(f"Avg completion time: {avg_completion:.2f} sec")
        print(f"Late %: {late_pct:.2f}")
        print(f"Human distance: {self.human_distance:.2f}")
        print(f"Human idle: {self.human_idle:.2f}")
        print(f"AMR idle: {self.amr_idle:.2f}")
        print(f"AMR utilization: {amr_util:.2f}%")
        print(f"Avg perishable exposure: {avg_exposure:.2f} sec")
        print(f"Throughput: {throughput:.2f} orders/hour")
