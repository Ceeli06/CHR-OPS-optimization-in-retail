import json
import os
import random
import copy

BASE_DIR = os.path.dirname(__file__)
DATASET_DIR = os.path.join(BASE_DIR, "DatasetAnalysis")

orders_path = os.path.join(DATASET_DIR, "orders.json")

with open(orders_path, "r") as f:
    orders = json.load(f)
    order_keys = list(orders.keys())

# returns a dictionary entry of an order
def generate_order():
    visit_id = random.choice(order_keys)
    order = copy.deepcopy(orders[visit_id])

    return {
        "visit_id": visit_id,
        "items": order
    }
