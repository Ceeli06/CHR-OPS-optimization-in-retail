import json
import os
import random
import copy
from setup_layout import CATEGORYMAPPING

BASE_DIR = os.path.dirname(__file__)
DATASET_DIR = os.path.join(BASE_DIR, "DatasetAnalysis")

orders_path = os.path.join(DATASET_DIR, "orders.json")
random.seed(12)  # keeps randomization constant

with open(orders_path, "r") as f:
    orders = json.load(f)
    order_keys = list(orders.keys())

# returns a dictionary entry of an order
def generate_order_helper():
    visit_id = random.choice(order_keys)
    order = copy.deepcopy(orders[visit_id])

    return {
        "visit_id": visit_id,
        "items": order
    }

# Creates explicit item coordinates for one order
# based on department mapping and quantity.
def generate_order_coords(items, coord_map):
    coords = []

    for item in items:
        department = item["department"]
        quantity = item["quantity"]
        mapped_department = CATEGORYMAPPING.get(department)

        if mapped_department is None:
            raise KeyError(f"Department mapping not found for '{department}'")

        possible_coords = coord_map.get(str(mapped_department), [])
        if not possible_coords:
            raise ValueError(
                f"No coordinates found for department '{department}' mapped to '{mapped_department}'"
            )

        for _ in range(quantity):
            coords.append(random.choice(possible_coords))

    return coords

# Builds a single order record with an optional arrival time.
def generate_order(coord_map, arrival_time=None):
    order = generate_order_helper()
    coords = generate_order_coords(order["items"], coord_map)

    return {
        "visit_id": order["visit_id"],
        "items": order["items"],
        "coords": coords,
        "arrival_time": arrival_time,
    }

# Creates a list of arrival times for a Poisson process.
def generate_order_arrival_times(sim_time, arrival_rate):
    times = []
    current_time = 0.0

    while True:
        interarrival = random.expovariate(arrival_rate)
        current_time += interarrival
        if current_time >= sim_time:
            break
        times.append(current_time)

    return times

# Generates orders for the simulation horizon.
def generate_orders(coord_map, sim_time, arrival_rate):
    arrival_times = generate_order_arrival_times(sim_time, arrival_rate)
    orders_list = []

    for order_id, arrival_time in enumerate(arrival_times):
        order = generate_order(coord_map, arrival_time=arrival_time)
        order["order_id"] = order_id
        orders_list.append(order)

    return orders_list