import params
import models
from orderGen import generate_orders
from setup_layout import setup_layout, map_of_coords, get_path, path_distance, all_distance_maps

URGENCY_THRESHOLD = 30 * 60 # Orders with remaining pick times under this threshold are auto-batched

# Main DES simulation, where time advances only when events occur (arrivals, dispatches, completions)
class DeadlineSim(models.Simulation):
    # Greedy order assignment, picking whichever picker becomes available earliest
    def select_picker(self):
        return min(self.pickers, key=lambda p: p.available_time)
    
    def select_amr(self):
        if len(self.amrs) == 0:
            return
        return min(self.amrs, key=lambda p: p.available_time) 

    # Adds orders to batch starting with orders that have reached the URGENCY_THRESHOLD,
    # then sorting remaining orders by due date and using Jaccard helper to greedily select
    # remaining orders based on similarity, eturning slected and the remaining orders
    def deadline_aware_batching(self, batch_size):
        if not self.pending_orders:
            return [], []

        urgent_orders = []
        non_urgent_orders = []

        # Filter orders by URGENCY_THRESHOLD
        for order in self.pending_orders:
            time_remaining = order.due_time - self.time
            if time_remaining <= URGENCY_THRESHOLD:
                urgent_orders.append(order)
            else:
                non_urgent_orders.append(order)

        # Sort urgent orders by due time
        urgent_orders.sort(key=lambda o: o.due_time)

        selected_orders = []
        
        # Pull as many urgent orders as will fit into the batch capacity
        while urgent_orders and len(selected_orders) < batch_size:
            selected_orders.append(urgent_orders.pop(0))

        # If batch is not full, select remaining based on due-time and similarity
        remaining_capacity = batch_size - len(selected_orders)
        if remaining_capacity > 0 and non_urgent_orders:
            # Sort non_urgent_orders by due time so the closest deadline becomes our seed
            non_urgent_orders.sort(key=lambda o: o.due_time)
            
            # Pop the oldest due order as our seed for Jaccard
            seed_order = non_urgent_orders.pop(0)
            selected_orders.append(seed_order)
            remaining_capacity -= 1
            
            # If we still have slots, sort the remaining non_urgent orders by similarity and append
            if remaining_capacity > 0 and non_urgent_orders:
                non_urgent_orders.sort(
                    key=lambda o: (-self.order_similarity(seed_order, o), o.due_time)
                )
                
                while non_urgent_orders and remaining_capacity > 0:
                    selected_orders.append(non_urgent_orders.pop(0))
                    remaining_capacity -= 1

        remaining_orders = urgent_orders + non_urgent_orders
        return selected_orders, remaining_orders
    
    # Decides batch size, then returns a batch of that size created via. deadline_aware_batching
    def create_batch(self, picker, amr, force=False):
        if not self.pending_orders:
            return None
        if not force and len(self.pending_orders) < params.BATCH_SIZE_MIN:
            return None

        batch_size = min(len(self.pending_orders), params.BATCH_SIZE_MAX)
        batch_orders, self.pending_orders = self.deadline_aware_batching(batch_size)
        amrId = None
        if (amr):
            amrId = amr.id

        batch = models.Batch(orders=batch_orders, picker_id = picker.id, amr_id = amrId)
        return batch
    
    # Builds a decoupled route that is not synchronized with another resource (other AMR/Picker)
    def build_decoupled_route(self, orders, start_node):
        coords = []
        for order in orders:
            coords.extend(order.coords)

        unique_coords = list(dict.fromkeys(coords))
        if not unique_coords:
            return [start_node]
        
        # New route starts directly from current location for pickers or from staging for amrs
        route = get_path(unique_coords, self.dist_map, start_node, self.map)
        return route[:-1]

    # Main order handling function which routes a batch, computes pick times, and schedules its completion
    def handle_batch(self, payload):
        final = isinstance(payload, dict) and payload.get("final", False)
        # A timeout event forces a dispatch if the oldest pending order has waited BATCH_TIMEOUT
        timeout = (
            isinstance(payload, dict) and payload.get("timeout", False)
            and self.pending_orders
            and self.time - self.pending_orders[0].arrival_time >= params.BATCH_TIMEOUT
        )
        force = final or timeout

        # Abort if batch is not valid
        if not force and len(self.pending_orders) < params.BATCH_SIZE_MIN:
            return

        # Check picker availability before pulling orders from pending_orders,
        # so a busy picker doesn't cause orders to be lost on reschedule
        picker = self.select_picker()
        amr = self.select_amr()

        # Schedules batch dispatch in the future if picker and/or AMR not available and returns 
        if picker.available_time > self.time or (amr != None and amr.available_time > self.time):
            amr_time = 0
            if (amr):
                amr_time = amr.available_time
            self.schedule(max(picker.available_time, amr_time), "BATCH_DISPATCH", payload)
            return

        batch = self.create_batch(picker, amr, force=force)
        if batch is None: # Invalid batch-catching
            return

        picker.mark_busy(self.time)
        if (amr):
            amr.mark_busy(self.time)

        all_coords = []
        for order in batch.orders:
            all_coords.extend(order.coords)
        unique_coords = list(dict.fromkeys(all_coords))

        meeting_point = self.staging # First coord of first item in batch (where AMR/picker meet)
        if (unique_coords):
            meeting_point = unique_coords[0]
        
        # Picker travels route starting at current location, while AMR always starts at staging
        picker_to_start_dist = path_distance([picker.location, meeting_point], self.dist_map)
        amr_to_start_dist = 0.0
        if (amr):
            amr_to_start_dist = path_distance([self.staging, meeting_point], self.dist_map)
        
        picker_arrival = self.time + (picker_to_start_dist / params.WALKING_SPEED)
        amr_arrival = self.time 
        if (amr):
            amr_arrival = self.time + (amr_to_start_dist / params.AMR_SPEED)

        # Picking starts at time when both AMR + picker have arrived at the first item
        sync_start_time = max(picker_arrival, amr_arrival)

        # Calculate human wait time if they beat the robot to the zone
        human_wait = max(0.0, amr_arrival - picker_arrival)
        self.metrics.human_wait_for_amr += human_wait
        self.metrics.human_idle += human_wait

        # Building decoupled routes
        route = self.build_decoupled_route(batch.orders, meeting_point)
        picking_distance = path_distance(route, self.dist_map)
        
        human_picking_time = picking_distance / params.WALKING_SPEED
        amr_picking_time = 0.0
        if (amr):
            amr_picking_time = picking_distance / params.AMR_SPEED

        # Sync baseline before sequential pick durations are added
        time_cursor = sync_start_time + max(human_picking_time, amr_picking_time)

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
        for node in route:
            if node == self.staging:
                break
            orders_at_node = coord_orders.get(node, [])
            if not orders_at_node:
                continue
            if (amr):
                pick_duration = len(orders_at_node) * params.AMR_LOAD_TIME
            else: 
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
                order.items_remaining -= decrement 

        last_item_location = self.staging
        if (route):
            last_item_location = route[-1]

        picker.location = last_item_location # Picker ends the order at the last item location
        picker.available_time = time_cursor
        picker.mark_idle(time_cursor) # Picker is free the instant picking ends, not when the AMR later reaches staging

        total_human_distance = picker_to_start_dist + picking_distance
        picker.distance_walked += total_human_distance
        self.metrics.human_distance += total_human_distance

        if (amr):
            # AMR ends the order at the staging location (drops items off dysynchronized from picker)
            amr_return_dist = path_distance([last_item_location, self.staging], self.dist_map)
            amr_return_time = amr_return_dist / params.AMR_SPEED
            amr.available_time = time_cursor + amr_return_time

            total_amr_distance = amr_to_start_dist + picking_distance + amr_return_dist
            self.metrics.amr_distance += total_amr_distance
            self.schedule(time_cursor + amr_return_time, "PICK_COMPLETE", batch)
        else:
            self.schedule(time_cursor, "PICK_COMPLETE", batch)

# Main experimentation space where testing occurs
if __name__ == "__main__":
    layout = setup_layout()  # Medium layout has all 16 departments, used as baseline before layout realism changes
    coord_map = map_of_coords(layout)
    dist_map = all_distance_maps(layout)  # Precompute distances for routing
    staging = coord_map["S"][0]

    # Precompute list of orders
    raw_orders = generate_orders(coord_map, params.SIM_TIME, layout, params.ORDER_ARRIVAL_RATE)
    orders = [
       models.Order(
            id=raw_order["order_id"],
            arrival_time=raw_order["arrival_time"],
            due_time =raw_order["arrival_time"] + params.ORDER_DUE_TIME,
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

    sim = DeadlineSim(orders, pickers, amrs, coord_map, staging=staging, dist_map=dist_map)
    sim.run()