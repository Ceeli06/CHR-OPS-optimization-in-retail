# Discrete-event simulation for a manual (human-only) retail order picking policy.
# Orders arrive via Poisson process, are batched (6-8 per cart), 
# assigned to a picker, and routed using a greedy nearest-neighbor heuristic algorithm

import params
from dataclasses import dataclass


# Orders waiting longer than this are forceed into the next batch
SIMILARITY_BATCH_MAX_WAIT = 90 * 60  # 1.5 hours

# If the pending queue has been non-empty this long without reaching BATCH_SIZE_MIN, force a dispatch
BATCH_TIMEOUT = 5 * 60  # 5 minutes

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
        amr_util = ((sim_time - self.amr_idle) / sim_time) * 100 if sim_time > 0 else 0.0

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
        print(f"Total AMR idle time: {self.amr_idle/60:.2f} min")
        print(f"AMR utilization: {amr_util:.2f}%")
        print(f"Avg perishable exposure time: {avg_exposure/60:.2f} min")
        print(f"Spoiled perishables: {spoiled_pct:.2f}%")
        print(f"Throughput: {throughput:.2f} orders/hour")

