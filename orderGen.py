import json
import os
import random
import copy
from setup_layout import CATEGORYMAPPING, map_of_coords, setup_large

BASE_DIR = os.path.dirname(__file__)
DATASET_DIR = os.path.join(BASE_DIR, "DatasetAnalysis")

orders_path = os.path.join(DATASET_DIR, "orders.json")
random.seed(121233) # keeps randomization constant

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
    list = []
    for items in order_set:
        dep = items['department']
        quantity = items['quantity']
        dep_num = str(CATEGORYMAPPING[dep])
        
        for i in range(quantity):
            possible_cords = coord_map[dep_num]
            x = random.randint(0, len(possible_cords) - 1)
            list.append(possible_cords[x])

    return {
        "visit_id": order['visit_id'],
        "items": order['items'],
        "coords": list
    }

def convert_coords(grid, coords):
    rows, cols = len(grid), len(grid[0])

    directions = [
        (-1, 0),  # up
        (1, 0),   # down
        (0, -1),  # left
        (0, 1),   # right
    ]

    converted = []

    for r, c in coords:
        found = False

        for dr, dc in directions:
            nr, nc = r + dr, c + dc

            if 0 <= nr < rows and 0 <= nc < cols:
                if grid[nr][nc] == '.':
                    converted.append((nr, nc))
                    found = True
                    break

        if not found:
            raise ValueError(f"No adjacent walkable cell for {(r, c)}")

    return converted
            

large = setup_large()
map = map_of_coords(large)
aisle_coords = generate_order(map)["coords"]
real_coords = convert_coords(large, aisle_coords)
print(aisle_coords)
print(real_coords)
