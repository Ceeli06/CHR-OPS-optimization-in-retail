import json
import os
import random
import copy
from setup_layout import CATEGORYMAPPING, map_of_coords, setup_small

BASE_DIR = os.path.dirname(__file__)
DATASET_DIR = os.path.join(BASE_DIR, "DatasetAnalysis")

orders_path = os.path.join(DATASET_DIR, "orders.json")
random.seed(12) # keeps randomization constant

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

def generate_order(coord_map):
    order = generate_order_helper()
    order_set = order['items']
    print(order_set)
    for items in order_set:
        list = []
        dep = items['department']
        quantity = items['quantity']
        dep_num = str(CATEGORYMAPPING[dep])
        
        for i in range(quantity):
            possible_cords = coord_map[dep_num]
            x = random.randint(0, len(possible_cords) - 1)
            list.append(possible_cords[x])
    print(list)

    return {
        "visit_id": order['visit_id'],
        "items": order['items'],
        "coords": list
    }
            

small = setup_small()
map = map_of_coords(small)
generate_order(map)
