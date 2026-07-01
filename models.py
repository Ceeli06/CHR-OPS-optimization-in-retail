import params
import heapq
from collections import defaultdict
from dataclasses import dataclass
from orderGen import generate_order_helper, generate_order_coords, convert_to_walkable
from setup_layout import path_distance, get_path
import math


# A customer order with items to be picked from the store
@dataclass
class Order:
    id: int
    arrival_time: float
    items: list  # List of dicts with "department" and "quantity" keys
    coords: list  # (row, col) locations of each item in the store
    is_perishable: bool = False

    pick_start_time: float = None  # When picker first touches this order's items
    at_staging_time: float = None  # When all items are picked

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
    distance_traveled: float = (
        0.0  # Enables desynchronized routing for deadlineAware scenario
    )
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


@dataclass
class Customer:
    id: int
    location: tuple  # current position of customer
    visits: dict = (
        None  # arrival_time and departure_time of coords for the customer's current trip
    )
    available_time: float = 0.0  # when current order is finished


# A set of orders grouped together for one picker to handle (6-8 per cart for manual)
@dataclass
class Batch:
    orders: list
    picker_id: int = None
    amr_id: int = None
    zoned_orders: dict = None


# Utilities to collect and print key performance metrics at end of simulation
class Metrics:
    def __init__(self):
        self.completion_times = (
            []
        )  # List of order completion times (comp.Time = comp.time - arrivalTime)
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
        self.amr_wait_for_human = 0.0
        self.amr_swap_count = 0
        self.batch_completion_count = 0
        self.last_batch_size = 0
        self.total_time_to_finish = 0

    # Record a completed order's comp.time and check if it missed the due time
    def record_completion(self, order: Order):
        final_completion_time = (
            order.at_staging_time - order.arrival_time
        ) + params.STAGING_TIME
        self.completion_times.append(final_completion_time)
        print("final_completion_time", order.id, final_completion_time)

        if final_completion_time > (order.due_time - order.arrival_time):
            self.late_orders += 1

        self.total_orders += 1

    # Compute and print all final metrics
    def finalize(self, sim_time: float, elapsed_time: float = None):
        elapsed_time = elapsed_time if elapsed_time is not None else sim_time
        self.total_time_to_finish = elapsed_time
        avg_completion = (
            sum(self.completion_times) / len(self.completion_times)
            if self.completion_times
            else 0.0
        )
        late_pct = (
            (self.late_orders / self.total_orders) * 100 if self.total_orders else 0.0
        )
        throughput = self.total_orders / (sim_time / 3600) if sim_time > 0 else 0.0

        # AMR utilization (ignored for manual policy since AMR not used)
        amr_util = (
            ((elapsed_time - ((self.amr_idle) / params.num_robots)) / elapsed_time)
            * 100
            if elapsed_time > 0 and params.num_robots
            else 0.0
        )

        avg_exposure = (
            sum(self.perishable_exposure) / len(self.perishable_exposure)
            if self.perishable_exposure
            else 0.0
        )

        spoiled_pct = (
            (self.spoiled_perishables / self.total_perishables) * 100
            if self.total_perishables
            else 0.0
        )

        avg_picker_distance = (
            self.human_distance / params.num_pickers if params.num_pickers else 0.0
        )
        avg_picker_idle = (
            self.human_idle / params.num_pickers if params.num_pickers else 0.0
        )
        avg_amr_wait_for_human = (
            self.amr_wait_for_human / params.num_robots if params.num_robots else 0.0
        )
        avg_amr_idle = self.amr_idle / params.num_robots if params.num_robots else 0.0

        # Stored on self so callers (e.g. simDashboard.py) can read the derived
        # metrics without re-deriving these formulas themselves
        self.avg_completion = avg_completion
        self.late_pct = late_pct
        self.throughput = throughput
        self.amr_util = amr_util
        self.avg_exposure = avg_exposure
        self.spoiled_pct = spoiled_pct
        self.avg_picker_distance = avg_picker_distance
        self.avg_picker_idle = avg_picker_idle
        self.avg_amr_wait_for_human = avg_amr_wait_for_human
        self.avg_amr_idle = avg_amr_idle

        print("\n===== METRICS =====")
        print(f"Avg completion time: {avg_completion/60:.2f} min")
        print(f"Late orders: {late_pct:.2f}%")
        print(f"Total picker travel distance: {self.human_distance:.2f} meters")
        print(f"Avg picker travel distance: {avg_picker_distance:.2f} meters")
        print(f"Total picker idle time: {self.human_idle/60:.2f} min")
        print(f"Avg picker idle time: {avg_picker_idle/60:.2f} min")
        print(f"Human wait time for AMR: {self.human_wait_for_amr/60:.2f} min")
        print(f"AMR wait time for human: {self.amr_wait_for_human/60:.2f} min")
        print(f"Avg AMR wait time for human: {avg_amr_wait_for_human/60:.2f} min")
        print(f"Total AMR idle time: {self.amr_idle/60:.2f} min")
        print(f"Avg AMR idle time: {avg_amr_idle/60:.2f} min")
        print(f"Average AMR utilization: {amr_util:.2f}%")
        print(f"Avg perishable exposure time: {avg_exposure/60:.2f} min")
        print(f"Spoiled perishables: {spoiled_pct:.2f}%")
        print(f"Throughput: {throughput:.2f} orders/hour")
        print(f"AMR Swap Count: {self.amr_swap_count}")
        print(f"Batch count: {self.batch_completion_count:.2f} batches")
        print(f"Flush Batch Size: {self.last_batch_size:.2f} orders")
        print(
            f"Completion time for all orders/end of sim: {self.total_time_to_finish/3600:.2f} hours"
        )


# Parent simulation class that policy-specific simulations inherit from
@dataclass
class Simulation:
    def __init__(
        self,
        orders,
        pickers,
        amrs,
        coord_map,
        layout=None,
        zoneFollow=False,
        staging=(0, 0),
        dist_map=None,
        customers=None,
    ):
        self.time = 0.0
        self.event_queue = (
            []
        )  # Event queue containing: (time, counter, event_type, payload)
        self.event_counter = 0  # Used so events with same arrival_time process FIFO

        self.orders = sorted(orders, key=lambda o: o.arrival_time)
        self.pending_orders = []  # Orders waiting to be batched
        self.pickers = pickers
        self.amrs = amrs  # Unused in manual policy
        self.customers = customers or []

        self.staging = staging
        self.dist_map = dist_map
        self.layout = layout
        self.metrics = Metrics()
        self.map = coord_map
        self.zoneFollow = zoneFollow

        # Sets up event queue for scheduling order events
        for order in self.orders:
            self.schedule(order.arrival_time, "ORDER_ARRIVAL", order)

        # Schedules customer arrivals and initial orders
        for customer in self.customers:
            self.schedule(0.0, "CUSTOMER_SHOPPING_COMPLETE", customer)

        self.schedule(params.SIM_TIME, "SIM_END_FLUSH", None)

    # Push an event onto the priority queue using heapq
    def schedule(self, time: float, event_type: str, payload):
        heapq.heappush(
            self.event_queue, (time, self.event_counter, event_type, payload)
        )
        self.event_counter += 1

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

        route = get_path(unique_coords, self.dist_map, startEnd, self.map, self.layout)
        if not route or route[-1] != startEnd:
            route.append(startEnd)

        return route

    # build_route for directFollow and manual
    def build_route_2(self, orders):
        coords = []
        for order in orders:
            coords.extend(order.coords)

        unique_coords = list(dict.fromkeys(coords))
        if not unique_coords:
            return [self.staging]

        route = get_path(
            unique_coords, self.dist_map, self.staging, self.map, self.layout
        )
        if not route or route[-1] != self.staging:
            route.append(self.staging)

        return route

    def build_zoning_route(self, zoned_orders):
        route = {}

        for zone_id, coords in zoned_orders.items():
            route[zone_id] = self.build_route(coords, self.handoffPoints[zone_id])

        return route

    # Greedy order assignment, picking whichever picker becomes available earliest
    def select_picker(self):
        return min(self.pickers, key=lambda p: p.available_time)

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
            elif event_type == "CUSTOMER_SHOPPING_COMPLETE":
                self.handle_customer_shopping(payload)
            elif event_type == "SIM_END_FLUSH":
                self.handle_end_flush()

        # Sum total idle times and output final metrics
        end_time = (
            self.time
        )  # actual final processed time, may exceed actual SIM_TIME due to flushed events
        for p in self.pickers:
            p.mark_busy(end_time)  # flush trailing idle into total_idle
        for r in self.amrs:
            r.mark_busy(end_time)
        self.metrics.human_idle = sum(p.total_idle for p in self.pickers)
        self.metrics.amr_idle = sum(r.total_idle for r in self.amrs)
        self.metrics.finalize(params.SIM_TIME, end_time)

    # Add an arriving order to the queue of pending orders, dispatching a batch once enough have piled up
    # Sends order to be batched
    def handle_order_arrival(self, order: Order):
        self.pending_orders.append(order)
        if len(self.pending_orders) >= params.BATCH_SIZE_MIN:
            self.schedule(self.time, "BATCH_DISPATCH", None)
        elif len(self.pending_orders) == 1:
            self.schedule(
                self.time + params.BATCH_TIMEOUT, "BATCH_DISPATCH", {"timeout": True}
            )

    # At sim end, push any remaining pending orders into a final batch
    def handle_end_flush(self):
        if self.pending_orders:
            self.metrics.last_batch_size = len(self.pending_orders)
            self.schedule(self.time, "BATCH_DISPATCH", {"final": True})

    # Customer generates a new random shopping order and walks it at CUSTOMER_SPEED, adding browse
    # time at each stop, then immediately schedules another trip when done
    def handle_customer_shopping(self, customer):
        if self.dist_map is None or self.layout is None:
            return

        order = generate_order_helper()
        coords = generate_order_coords(order["items"], self.map, self.layout)
        if not coords:
            self.schedule(self.time + 60.0, "CUSTOMER_SHOPPING_COMPLETE", customer)
            return

        visits = {}
        time_cursor = self.time
        prev_node = customer.location
        for coord in coords:
            travel_dist = self.dist_map[prev_node][coord[0], coord[1]]
            time_cursor += travel_dist / params.CUSTOMER_SPEED
            arrival = time_cursor
            time_cursor += params.CUSTOMER_BROWSE_TIME
            visits[coord] = (arrival, time_cursor)
            prev_node = coord

        customer.visits = visits
        customer.location = coords[-1]
        customer.available_time = time_cursor
        self.schedule(customer.available_time, "CUSTOMER_SHOPPING_COMPLETE", customer)

    # Checks whether an AMR occupying coord from arrival_time to departure_time overlaps (within
    # CUSTOMER_COLLISION_BUFFER seconds of margin) any customer's visit window at that same coord
    def customer_collisions(self, coord, arrival_time, departure_time):
        pause = 0.0
        for customer in self.customers:
            if not customer.visits:
                continue
            visit = customer.visits.get(coord)
            if not visit:
                continue
            cust_arrival, cust_departure = visit
            if (
                arrival_time - params.CUSTOMER_COLLISION_BUFFER <= cust_departure
                and cust_arrival <= departure_time + params.CUSTOMER_COLLISION_BUFFER
            ):
                pause += params.CUSTOMER_COLLISION_TIME
        return pause

    # Helper that returns a set of all department names in an order
    def department_set(self, order):
        return {str(item.get("department", "")).lower() for item in order.items}

    # Gets Jaccard similarity of two order's department sets (0 = completely different, 1 = identical)
    def order_similarity(self, a, b):
        depts_a = a
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

        total_intersection = self.department_set(seed)
        for i in selected_indices:
            dept_next = self.department_set(self.pending_orders[i])
            total_intersection = total_intersection | dept_next

        # Greedily fill remaining slots via. Jaccard with orders most similar to what is alr included in the batch (seed)
        remaining_indices = [
            i for i in range(1, len(self.pending_orders)) if i not in selected_indices
        ]

        remaining_indices.sort(
            key=lambda i: (
                -self.order_similarity(total_intersection, self.pending_orders[i]),
                i,
            )
        )

        for i in remaining_indices:
            if len(selected_indices) >= batch_size:
                break
            selected_indices.add(i)

        selected_orders = [self.pending_orders[i] for i in sorted(selected_indices)]
        remaining_orders = [
            order
            for i, order in enumerate(self.pending_orders)
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
            is_perishable_item = (
                str(item.get("department", "")).lower().find("perishable") >= 0
            )
            for _ in range(quantity):
                coord = next(coords_iter)
                if is_perishable_item:
                    perishable_coords.add(coord)
        return perishable_coords

    # Distance,time, and unload time for an AMR returning to staging from from a node, carrying 'items_carried' items
    def amr_return_leg(self, from_node, items_carried):
        return_dist = path_distance([from_node, self.staging], self.dist_map)
        return_time = return_dist / params.AMR_SPEED
        unload_time = params.AMR_AND_CART_UNLOAD_TIME * items_carried
        return return_dist, return_time, unload_time

    # Mark picker idle, record metrics for the completed batch, and schedule the next one if ready
    def handle_pick_complete(self, batch):
        # Update picker
        if batch.picker_id is not None:
            picker = self.pickers[batch.picker_id]
            picker.mark_idle(self.time)
        if batch.amr_id is not None:
            amr = self.amrs[batch.amr_id]
            amr.mark_idle(self.time)

        # Record order completion times and perishible exposure times
        for order in batch.orders:
            if order.at_staging_time is None:
                order.at_staging_time = self.time
            self.metrics.record_completion(order)
            if order.is_perishable:
                if order.perishable_picked_at is not None:
                    start_time = order.perishable_picked_at
                # elif and else below this line should never be executed but is here for fallback
                elif order.pick_start_time is not None:
                    start_time = order.pick_start_time
                else:
                    start_time = order.arrival_time

                exposure = order.at_staging_time - start_time
                print("exposure", exposure)
                print(start_time)
                print(order.at_staging_time)
                item_count = self.perishable_item_count(order)

                self.metrics.perishable_exposure.extend([exposure] * item_count)

                self.metrics.total_perishables += item_count
                if exposure > params.FREEZER_PERISHABLE_TIME:
                    self.metrics.spoiled_perishables += item_count

        # Schedule next batch if enough orders
        if len(self.pending_orders) >= params.BATCH_SIZE_MIN:
            self.schedule(self.time, "BATCH_DISPATCH", None)
