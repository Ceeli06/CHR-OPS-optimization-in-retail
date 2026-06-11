import math
import random
import heapq
import params
from collections import deque
from dataclasses import dataclass


# =========================
# SIMULATION SETTINGS
# =========================

SIM_TIME = 8 * 60 * 60
ORDER_DUE_TIME = params.ORDER_DUE_TIME


# =========================
# ENTITIES
# =========================

@dataclass
class Order:
    id: int
    arrival_time: float
    items: list
    is_perishable: bool = False

    pick_time: float = None
    completion_time: float = None


@dataclass
class Picker:
    id: int
    location: tuple
    available_time: float = 0
    is_idle: bool = True
    idle_start: float = 0
    total_idle: float = 0
    distance_walked: float = 0


@dataclass
class AMR:
    id: int
    location: tuple
    available_time: float = 0
    is_idle: bool = True
    idle_start: float = 0
    total_idle: float = 0


@dataclass
class Batch:
    orders: list
    picker_id: int


# =========================
# METRICS
# =========================

class Metrics:

    def __init__(self):
        self.completion_times = []
        self.late_orders = 0
        self.total_orders = 0

        self.human_distance = 0
        self.amr_distance = 0

        self.human_idle = 0
        self.amr_idle = 0

        self.perishable_exposure = []

    def record_completion(self, order: Order):

        t = order.completion_time - order.arrival_time
        self.completion_times.append(t)

        if t > params.ORDER_DUE_TIME:
            self.late_orders += 1

        self.total_orders += 1

    def finalize(self, sim_time):

        avg_completion = sum(self.completion_times) / len(self.completion_times)
        late_pct = (self.late_orders / self.total_orders) * 100
        throughput = self.total_orders / (sim_time / 3600)

        amr_util = ((sim_time - self.amr_idle) / sim_time) * 100

        avg_exposure = (
            sum(self.perishable_exposure) / len(self.perishable_exposure)
            if self.perishable_exposure else 0
        )

        print("\n===== METRICS =====")
        print("Avg completion time:", avg_completion)
        print("Late %:", late_pct)
        print("Human distance:", self.human_distance)
        print("AMR idle:", self.amr_idle)
        print("AMR utilization:", amr_util)
        print("Avg perishable exposure:", avg_exposure)
        print("Throughput:", throughput)


# =========================
# UTILS
# =========================

def euclidean(a, b):
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)


# =========================
# SIMULATION CORE
# =========================

class Simulation:

    def __init__(self, orders, pickers, amrs, staging=(0, 0)):

        self.time = 0
        self.event_queue = []

        self.orders = deque(sorted(orders, key=lambda o: o.arrival_time))
        self.pending_orders = []

        self.pickers = pickers
        self.amrs = amrs

        self.staging = staging
        self.metrics = Metrics()

    # -------------------------
    # EVENT SYSTEM
    # -------------------------

    def schedule(self, time, event_type, payload):
        heapq.heappush(self.event_queue, (time, event_type, payload))

    def run(self):

        # seed arrivals
        while self.orders:
            o = self.orders.popleft()
            self.schedule(o.arrival_time, "ORDER_ARRIVAL", o)

        while self.event_queue and self.time < SIM_TIME:

            self.time, event_type, payload = heapq.heappop(self.event_queue)

            if event_type == "ORDER_ARRIVAL":
                self.handle_order_arrival(payload)

            elif event_type == "BATCH_DISPATCH":
                self.handle_batch(payload)

            elif event_type == "PICK_COMPLETE":
                self.handle_pick_complete(payload)

        self.metrics.finalize(self.time)

    # -------------------------
    # ORDER ARRIVAL
    # -------------------------

    def handle_order_arrival(self, order):
        self.pending_orders.append(order)

        if len(self.pending_orders) >= 6:
            batch = self.create_batch()
            self.schedule(self.time, "BATCH_DISPATCH", batch)

    # -------------------------
    # BATCHING
    # -------------------------

    def create_batch(self):

        batch_orders = self.pending_orders[:8]
        self.pending_orders = self.pending_orders[8:]

        picker = self.select_picker()

        return Batch(
            orders=batch_orders,
            picker_id=picker.id
        )

    # greedy picker selection
    def select_picker(self):
        return min(self.pickers, key=lambda p: p.available_time)

    # -------------------------
    # HANDLE BATCH
    # -------------------------

    def handle_batch(self, batch):

        picker = self.pickers[batch.picker_id]

        if picker.available_time > self.time:
            self.schedule(picker.available_time, "BATCH_DISPATCH", batch)
            return

        picker.is_idle = False

        travel_time = 0
        pick_time = 0

        for order in batch.orders:

            travel_time += euclidean(picker.location, self.staging)
            picker.location = self.staging

            pick_time += len(order.items) * params.HUMAN_PICK_TIME

            order.pick_time = self.time + travel_time + pick_time

        picker.distance_walked += travel_time
        self.metrics.human_distance += travel_time

        finish_time = self.time + travel_time + pick_time

        picker.available_time = finish_time

        self.schedule(finish_time, "PICK_COMPLETE", batch)

    # -------------------------
    # PICK COMPLETE
    # -------------------------

    def handle_pick_complete(self, batch):

        for order in batch.orders:

            order.completion_time = self.time
            self.metrics.record_completion(order)

        picker = self.pickers[batch.picker_id]
        picker.is_idle = True


# =========================
# ENTRY POINT
# =========================

if __name__ == "__main__":

    orders = [
        Order(i, i * 30, [{"item": 1}] * random.randint(1, 5))
        for i in range(40)
    ]

    pickers = [Picker(0, (0, 0))]
    amrs = [AMR(0, (0, 0))]

    sim = Simulation(orders, pickers, amrs)
    sim.run()