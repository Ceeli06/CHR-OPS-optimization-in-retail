import heapq
import params
from dataclasses import dataclass
from orderGen import generate_orders
from setup_layout import setup_small, setup_medium, map_of_coords, nearest_neighbor, path_distance, all_distance_maps


# =========================
# ENTITIES
# =========================

@dataclass
class Order:
    id: int
    arrival_time: float
    items: list
    coords: list
    is_perishable: bool = False

    pick_start_time: float = None
    completion_time: float = None


@dataclass
class Picker:
    id: int
    location: tuple
    available_time: float = 0.0
    is_idle: bool = True
    idle_start: float = 0.0
    total_idle: float = 0.0
    distance_walked: float = 0.0

    def mark_busy(self, current_time: float):
        if self.is_idle:
            self.total_idle += current_time - self.idle_start
            self.is_idle = False

    def mark_idle(self, current_time: float):
        if not self.is_idle:
            self.is_idle = True
            self.idle_start = current_time


@dataclass
class AMR:
    id: int
    location: tuple
    available_time: float = 0.0
    is_idle: bool = True
    idle_start: float = 0.0
    total_idle: float = 0.0


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

        self.human_distance = 0.0
        self.amr_distance = 0.0

        self.human_idle = 0.0
        self.amr_idle = 0.0

        self.perishable_exposure = []

    def record_completion(self, order: Order):
        completion_delay = order.completion_time - order.arrival_time
        self.completion_times.append(completion_delay)

        if completion_delay > params.ORDER_DUE_TIME:
            self.late_orders += 1

        self.total_orders += 1

    def finalize(self, sim_time: float):
        avg_completion = sum(self.completion_times) / len(self.completion_times) if self.completion_times else 0.0
        late_pct = (self.late_orders / self.total_orders) * 100 if self.total_orders else 0.0
        throughput = self.total_orders / (sim_time / 3600) if sim_time > 0 else 0.0

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


# =========================
# SIMULATION CORE
# =========================

class Simulation:

    def __init__(self, orders, pickers, amrs, staging=(0, 0), dist_map=None):
        self.time = 0.0
        self.event_queue = []
        self.event_counter = 0

        self.orders = sorted(orders, key=lambda o: o.arrival_time)
        self.pending_orders = []

        self.pickers = pickers
        self.amrs = amrs

        self.staging = staging
        self.dist_map = dist_map
        self.metrics = Metrics()

    def schedule(self, time: float, event_type: str, payload):
        heapq.heappush(self.event_queue, (time, self.event_counter, event_type, payload))
        self.event_counter += 1

    def run(self):
        for order in self.orders:
            self.schedule(order.arrival_time, "ORDER_ARRIVAL", order)

        self.schedule(params.SIM_TIME, "SIM_END_FLUSH", None)

        while self.event_queue:
            self.time, _, event_type, payload = heapq.heappop(self.event_queue)
            if self.time > params.SIM_TIME:
                break

            if event_type == "ORDER_ARRIVAL":
                self.handle_order_arrival(payload)
            elif event_type == "BATCH_DISPATCH":
                self.handle_batch(payload)
            elif event_type == "PICK_COMPLETE":
                self.handle_pick_complete(payload)
            elif event_type == "SIM_END_FLUSH":
                self.handle_end_flush()

        self.metrics.human_idle = sum(p.total_idle for p in self.pickers)
        self.metrics.amr_idle = sum(r.total_idle for r in self.amrs)
        self.metrics.finalize(params.SIM_TIME)

    def handle_order_arrival(self, order: Order):
        self.pending_orders.append(order)
        if len(self.pending_orders) >= params.BATCH_SIZE_MIN:
            self.schedule(self.time, "BATCH_DISPATCH", None)

    def handle_end_flush(self):
        if self.pending_orders:
            self.schedule(self.time, "BATCH_DISPATCH", {"final": True})

    def create_batch(self, final=False):
        if not final and len(self.pending_orders) < params.BATCH_SIZE_MIN:
            return None

        batch_size = min(len(self.pending_orders), params.BATCH_SIZE_MAX)
        batch_orders = self.pending_orders[:batch_size]
        self.pending_orders = self.pending_orders[batch_size:]

        picker = self.select_picker()
        return Batch(orders=batch_orders, picker_id=picker.id)

    def select_picker(self):
        return min(self.pickers, key=lambda p: p.available_time)

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

    def handle_batch(self, payload):
        final = isinstance(payload, dict) and payload.get("final", False)
        batch = self.create_batch(final=final)
        if batch is None:
            return

        picker = self.pickers[batch.picker_id]
        if picker.available_time > self.time:
            self.schedule(picker.available_time, "BATCH_DISPATCH", payload)
            return

        picker.mark_busy(self.time)

        route = self.build_route(batch.orders)
        travel_distance = path_distance(route, self.dist_map)
        travel_time = travel_distance / params.WALKING_SPEED

        time_cursor = self.time
        time_cursor += travel_time

        coord_orders = {}
        for order in batch.orders:
            order.pick_start_time = None
            order.items_remaining = len(order.coords)
            order.is_perishable = any(
                str(item.get("department", "")).lower().find("perishable") >= 0
                for item in order.items
            )
            for coord in order.coords:
                coord_orders.setdefault(coord, []).append(order)

        for node in route[1:]:
            if node == self.staging:
                break
            orders_at_node = coord_orders.get(node, [])
            if not orders_at_node:
                continue

            pick_duration = len(orders_at_node) * params.HUMAN_PICK_TIME
            if pick_duration > 0:
                time_cursor += pick_duration

            # Remove duplicates while preserving order
            seen_orders = {}
            for order in orders_at_node:
                seen_orders[id(order)] = order
            
            for order in seen_orders.values():
                if order.pick_start_time is None:
                    order.pick_start_time = time_cursor - pick_duration
                decrement = sum(1 for coord in order.coords if coord == node)
                order.items_remaining -= decrement
                if order.items_remaining <= 0 and order.completion_time is None:
                    order.completion_time = time_cursor

        picker.distance_walked += travel_distance
        self.metrics.human_distance += travel_distance

        finish_time = time_cursor
        picker.available_time = finish_time
        self.schedule(finish_time, "PICK_COMPLETE", batch)

    def handle_pick_complete(self, batch):
        picker = self.pickers[batch.picker_id]
        picker.mark_idle(self.time)

        for order in batch.orders:
            if order.completion_time is None:
                order.completion_time = self.time
            self.metrics.record_completion(order)
            if order.is_perishable:
                start_time = order.pick_start_time if order.pick_start_time is not None else order.arrival_time
                self.metrics.perishable_exposure.append(order.completion_time - start_time)

        if len(self.pending_orders) >= params.BATCH_SIZE_MIN:
            self.schedule(self.time, "BATCH_DISPATCH", None)


# =========================
# ENTRY POINT
# =========================

if __name__ == "__main__":
    layout = setup_medium()  # Use medium layout which has all departments
    coord_map = map_of_coords(layout)
    dist_map = all_distance_maps(layout)
    staging = coord_map["S"][0]

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