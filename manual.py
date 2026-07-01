# Discrete-event simulation for a manual (human-only) retail order picking policy.
# Orders arrive via Poisson process, are batched (6-8 per cart),
# assigned to a picker, and routed using a greedy nearest-neighbor heuristic algorithm

import params
import models
from orderGen import generate_orders
from setup_layout import (
    setup_layout,
    map_of_coords,
    path_distance,
    all_distance_maps,
)


# Main DES simulation, where time advances only when events occur (arrivals, dispatches, completions)
class ManualSim(models.Simulation):

    # Creates a batch of orders to be completed together. The total batch size depends on BATCH_SIZE_MIN
    # and BATCH_SIZE_MAX constants. Orders are put together into a batch based on their item similarity.
    # Assigns the passed picker to the batch.
    def create_batch(self, picker, force=False):
        if not self.pending_orders:
            return None
        if not force and len(self.pending_orders) < params.BATCH_SIZE_MIN:
            return None

        batch_size = min(len(self.pending_orders), params.BATCH_SIZE_MAX)
        batch_orders, self.pending_orders = super().select_similar_batch(batch_size)

        return models.Batch(orders=batch_orders, picker_id=picker.id, amr_id=None)

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
        if picker.available_time > self.time:
            self.schedule(picker.available_time, "BATCH_DISPATCH", payload)
            return

        batch = self.create_batch(picker, force=force)
        if batch is None:  # Check for invalid batch-catching
            return

        picker.mark_busy(self.time)
        route = self.build_route_2(batch.orders)

        total_travel_distance = path_distance(route, self.dist_map)

        time_cursor = self.time  # Holds time from batch start at initialization
        item_count = 0

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
        prevNode = self.staging
        for node in route[1:-1]:
            if node == self.staging:
                break
            orders_at_node = coord_orders.get(node, [])
            if not orders_at_node:
                continue

            r, c = node
            dist_traveled = self.dist_map[prevNode][r, c]
            time_cursor += dist_traveled / (
                params.WALKING_SPEED * params.MANUAL_PUSH_FACTOR
            )
            prevNode = node

            pick_duration = len(orders_at_node) * (
                params.HUMAN_PICK_TIME + params.CART_LOAD_TIME
            )

            if pick_duration > 0:
                time_cursor += pick_duration  # Update batch time every pick
            item_count += len(orders_at_node)
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
            # Checks if cart capacity is exceeded
            if item_count > params.AMR_AND_CART_CAPACITY:
                # Same human returns back to staging and goes back if the cart capacity is exceeded
                r, c = self.staging
                dist_to_staging_and_back = 2 * self.dist_map[prevNode][r, c]
                picker.distance_walked += dist_to_staging_and_back
                time_cursor += dist_to_staging_and_back / params.WALKING_SPEED
                time_cursor += (
                    params.AMR_AND_CART_UNLOAD_TIME * item_count
                )  # Unload time
                self.metrics.amr_swap_count += 1

                item_count = 0

        # accounts for time traveling from last node to staging
        r, c = self.staging
        dist_last_node_to_staging = self.dist_map[prevNode][r, c]
        time_cursor += dist_last_node_to_staging / (
            params.WALKING_SPEED * params.MANUAL_PUSH_FACTOR
        )
        time_cursor += (
            params.AMR_AND_CART_UNLOAD_TIME * item_count
        )  # Unloading time @ end (picker must be present)

        # Update walking distance of picker and global total
        picker.distance_walked += total_travel_distance
        self.metrics.human_distance += total_travel_distance

        # Update picker avalible time and schedule a pick complete event
        finish_time = time_cursor
        picker.available_time = finish_time
        self.metrics.batch_completion_count += 1
        for order in batch.orders:
            if order.items_remaining <= 0 and order.at_staging_time is None:
                order.at_staging_time = finish_time

        self.schedule(finish_time, "PICK_COMPLETE", batch)


# Main experimentation space where testing occurs
if __name__ == "__main__":
    layout = setup_layout()
    coord_map = map_of_coords(layout)
    dist_map = all_distance_maps(layout)  # Precompute distances for routing
    staging = coord_map["S"][0]  # Finds coord of staging

    # Precompute list of orders
    raw_orders = generate_orders(
        coord_map, params.SIM_TIME, layout, params.order_arrival_rate
    )

    orders = [
        models.Order(
            id=raw_order["order_id"],
            arrival_time=raw_order["arrival_time"],
            due_time=raw_order["due_time"],
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

    sim = ManualSim(
        orders,
        pickers,
        amrs,
        coord_map,
        staging=staging,
        dist_map=dist_map,
        layout=layout,
    )
    sim.run()
