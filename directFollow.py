import params
import models
from orderGen import generate_orders
from setup_layout import (
    setup_layout,
    map_of_coords,
    get_path,
    path_distance,
    all_distance_maps,
)


# Main DES simulation, where time advances only when events occur (arrivals, dispatches, completions)
class FollowSim(models.Simulation):
    # Decides batch size, then returns a batch of that size created via. select_similar_batch
    def create_batch(self, picker, amr, force=False):
        if not self.pending_orders:
            return None
        if not force and len(self.pending_orders) < params.BATCH_SIZE_MIN:
            return None

        batch_size = min(len(self.pending_orders), params.BATCH_SIZE_MAX)
        batch_orders, self.pending_orders = self.select_similar_batch(batch_size)
        amrId = None
        if amr:
            amrId = amr.id

        batch = models.Batch(orders=batch_orders, picker_id=picker.id, amr_id=amrId)
        return batch 

    # Main order handling function which routes a batch, computes pick times, and schedules its completion
    def handle_batch(self, payload):
        final = isinstance(payload, dict) and payload.get("final", False)
        # A timeout event forces a dispatch if the oldest pending order has waited BATCH_TIMEOUT
        timeout = (
            isinstance(payload, dict)
            and payload.get("timeout", False)
            and self.pending_orders
            and self.time - self.pending_orders[0].arrival_time >= params.BATCH_TIMEOUT
        )
        force = final or timeout

        # Abort if batch is not valid
        if not force and len(self.pending_orders) < params.BATCH_SIZE_MIN:
            return

        # Check picker availability before pulling orders from pending_orders,
        # so a busy picker doesn't cause orders to be lost on reschedule
        picker = super().select_picker()
        amr = super().select_amr()

        # Schedules batch dispatch in the future if picker and/or AMR not available and returns
        if picker.available_time > self.time or (
            amr != None and amr.available_time > self.time
        ):
            time = 0
            if amr:
                time = amr.available_time
            self.schedule(max(picker.available_time, time), "BATCH_DISPATCH", payload)
            return

        batch = self.create_batch(picker, amr, force=force)
        if batch is None:  # Invalid batch-catching
            return

        picker.mark_busy(self.time)
        if amr:
            amr.mark_busy(self.time)
        self.metrics.batch_completion_count += 1
        route = super().build_route(batch.orders)
        travel_distance = path_distance(route, self.dist_map)
        human_travel_time = travel_distance / params.WALKING_SPEED
        amr_travel_time = travel_distance / params.AMR_SPEED

        time_cursor = self.time + max(
            human_travel_time, amr_travel_time
        )  # Holds time from batch start to end

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

        items_carried = 0
        last_amr_node = self.staging

        # Walk the route, picking items and updating order state at each stop
        for node in route[
            1:-1
        ]:  # ignores picking @ staging which is at the start and end of each route
            if node == self.staging:
                break
            orders_at_node = coord_orders.get(node, [])
            if not orders_at_node:
                continue
            if amr:
                pick_duration = len(orders_at_node) * params.CART_LOAD_TIME
                pick_duration += self.customer_collisions(
                    node, time_cursor, time_cursor + pick_duration
                )
            else:
                pick_duration = len(orders_at_node) * params.HUMAN_PICK_TIME
            if pick_duration > 0:
                time_cursor += pick_duration  # Update batch time every pick

            seen_orders = {}
            for order in orders_at_node:  # Add all items at node to "seen orders"
                seen_orders[id(order)] = order
            for order in seen_orders.values():  # For each item in "seen orders"
                if order.pick_start_time is None:
                    order.pick_start_time = time_cursor - pick_duration
                if (
                    order.is_perishable
                    and order.perishable_picked_at is None
                    and node in order.perishable_coords
                ):
                    order.perishable_picked_at = time_cursor - pick_duration
                decrement = sum(1 for coord in order.coords if coord == node)
                order.items_remaining -= decrement  # Decrement items remaining in batch

            if amr:
                items_carried += sum(
                    1
                    for order in orders_at_node
                    for coord in order.coords
                    if coord == node
                )
                last_amr_node = node

                if items_carried >= params.AMR_CAPACITY:
                    # Full AMR heads back to staging to unload; doesn't block the picker
                    return_dist, return_time, unload_time = self.amr_return_leg(
                        node, items_carried
                    )
                    amr.available_time = time_cursor + return_time + unload_time
                    amr.mark_idle(amr.available_time)
                    self.metrics.amr_distance += return_dist

                    # AMR is replaced with the most recently available AMR (could be the same AMR)
                    candidates = [a for a in self.amrs]
                    replacement = min(candidates, key=lambda a: a.available_time)
                    swap_dist = path_distance([self.staging, node], self.dist_map)
                    swap_wait = (
                        max(0.0, replacement.available_time - time_cursor)
                        + swap_dist / params.AMR_SPEED
                    )
                    time_cursor += swap_wait
                    self.metrics.human_wait_for_amr += swap_wait
                    self.metrics.human_idle += swap_wait
                    self.metrics.amr_distance += swap_dist
                    self.metrics.amr_swap_count += 1

                    replacement.mark_busy(time_cursor)
                    amr = replacement
                    items_carried = 0

        # Update walking distance of picker and global total
        picker.distance_walked += travel_distance
        self.metrics.human_distance += travel_distance
        self.metrics.human_wait_for_amr += max(0, amr_travel_time - human_travel_time)
        self.metrics.human_idle += max(0, amr_travel_time - human_travel_time)
        finish_time = time_cursor

        # order is not complete until entire batch is returned to staging
        for order in batch.orders:
            if order.items_remaining == 0 and order.completion_time is None:
                order.completion_time = finish_time

        # Update picker available time; whichever AMR is currently active still has to
        # travel back to staging and unload before it's free for its next dispatch

        picker.available_time = finish_time
        pick_complete_time = finish_time
        if amr:
            return_dist, return_time, unload_time = self.amr_return_leg(
                last_amr_node, items_carried
            )
            amr.available_time = time_cursor + return_time + unload_time
            amr.mark_idle(amr.available_time)
            self.metrics.amr_distance += return_dist
            pick_complete_time = max(finish_time, amr.available_time)
        self.schedule(pick_complete_time, "PICK_COMPLETE", batch)


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
    customers = [models.Customer(i, staging) for i in range(params.num_customers)]

    sim = FollowSim(
        orders,
        pickers,
        amrs,
        coord_map,
        staging=staging,
        dist_map=dist_map,
        customers=customers,
        layout=layout,
    )
    sim.run()
