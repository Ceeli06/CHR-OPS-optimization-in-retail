'''
Provides utilities for order generation based on the cleaned dataset from DatasetAnalysis
'''

import json
import os
import random
import copy
import params
from setup_layout import CATEGORYMAPPING

# Get and set the directory path for historical order dataset
BASE_DIR = os.path.dirname(__file__)
DATASET_DIR = os.path.join(BASE_DIR, "DatasetAnalysis")
orders_path = os.path.join(DATASET_DIR, "orders.json")

# Fixed random seed for order sets
random.seed(12)

# Load all orders from JSON dataset
with open(orders_path, "r") as f:
    orders = json.load(f)  # Dict mapping order IDs to items
    order_keys = list(orders.keys())  # List of all order IDs


# Picks a random order and returns it as a dict with order id
#                           and items in order as a list of {dept, qty}
def generate_order_helper():
    visit_id = random.choice(order_keys)
    order = copy.deepcopy(orders[visit_id])  # Use deepcopy to not modify orginal
    return {"visit_id": visit_id, "items": order}


# Gets and returns a list of coordinates for each item in an order
def generate_order_coords(items, coord_map, layout):
    coords = []
    for item in items:
        department = item["department"]
        quantity = item["quantity"]
        # Convert dept name to grid symbol (e.g., "Grocery" -> "1")
        mapped_department = CATEGORYMAPPING.get(department)
        if mapped_department is None:
            raise KeyError(f"Department mapping not found for '{department}'")
        possible_coords = coord_map.get(str(mapped_department), [])
        if not possible_coords:
            raise ValueError(
                f"No coordinates found for department '{department}' mapped to '{mapped_department}'"
            )
        # For each item, pick a random location in that department
        for _ in range(quantity):
            coord = random.choice(possible_coords)
            coord = convert_to_walkable(
                coord, layout
            )  # converts the unwalkable aisle location to an actual walkable location for the picker to go to
            coords.append(coord)

    return coords

# Takes the coordinates of an item and returns the picking location for that item
def convert_to_walkable(coord, layout):
    r, c = coord
    rows, cols = layout.shape

    # Already walkable
    if layout[r, c] == ".":
        return (r, c)

    directions = [
        (-1, 0),  # up
        (1, 0),  # down
        (0, -1),  # left
        (0, 1),  # right
    ]

    for dr, dc in directions:
        nr, nc = r + dr, c + dc

        if 0 <= nr < rows and 0 <= nc < cols and layout[nr, nc] == ".":
            return (nr, nc)


# Generate random order arrival times using a Poisson process (with exponential inter arrival times)
def generate_order_arrival_times(sim_time, arrival_rate):
    times = []
    current_time = 0.0  # Gen. at start of sim

    while True:
        interarrival = random.expovariate(arrival_rate)  # Gen. random interarrival time
        current_time += (
            interarrival  # Arrival time = current time + generated int.arr. time
        )

        if current_time >= sim_time:  # If end of sim reached, break
            break

        times.append(current_time)  # Append Arrival time to list

    return times  # Return all arrival times of entire sim


# Generates a complete order with items, store coordinates, and arrival time
def generate_order(coord_map, layout, arrival_time=None):
    order = generate_order_helper()
    coords = generate_order_coords(order["items"], coord_map, layout)
    due_window = random.uniform(
        params.ORDER_DUE_TIME_MIN, params.ORDER_DUE_TIME_MAX
    )  # Amount of time to fulfill the order

    return {
        "visit_id": order["visit_id"],
        "items": order["items"],
        "coords": coords,
        "arrival_time": arrival_time,
        "due_time": (
            arrival_time + due_window if arrival_time is not None else None
        ),  # Actual time the order is due
    }


# Generate all orders for the entire simulation, sorted by arrival time as a list
def generate_orders(coord_map, sim_time, layout, arrival_rate):
    arrival_times = generate_order_arrival_times(sim_time, arrival_rate)
    orders_list = []

    for order_id, arrival_time in enumerate(arrival_times):
        order = generate_order(coord_map, layout, arrival_time=arrival_time)
        order["order_id"] = order_id
        orders_list.append(order)

    return orders_list
