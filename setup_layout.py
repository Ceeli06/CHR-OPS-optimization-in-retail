# Includes functions for grabbing large, medium, and small store layouts from file input
# Includes mapping function to create dictionary of aisle -> coordinates, which will be used for order generation

import numpy as np
from collections import defaultdict, deque

# Key for understanding .txt file to layout conversion
# . is walking space
CATEGORYMAPPING = {
    "Grocery": 1,
    "Perishable Grocery": 2,
    "Health & Beauty": 3,
    "Misc.": 4,
    "Fashion": 5,
    "Home": 6,
    "Cleaning": 7,
    "Toys": 8,
    "Pets": 9,
    "Electronics": "A",
    "Garden": "B",
    "Home Improvement": "C",
    "Auto": "D",
    "Sports & Outdoors": "E",
    "Arts & Crafts": "F",
    "Staging": "S"
}

# Converts layout from string to a 2D array
def layout_to_array(layout_text):
    rows = [list(row) for row in layout_text.strip().splitlines()]
    return np.array(rows, dtype=str)

# Returns a map of coordinates for where specific types of item could be found
def map_of_coords(layout):
    coord_map = defaultdict(list)
    rows, cols = layout.shape
    for r in range(rows):
        for c in range(cols):
            value = str(layout[r, c]) # cast b/c np uses diff type of string

            if value != '.':
                coord_map[value].append((r, c))

    return dict(coord_map)

def setup_large():
    with open("Layouts/large.txt", "r") as f:
        large_layout = f.read() 
        return layout_to_array(large_layout)
def setup_medium():
    with open("Layouts/medium.txt", "r") as f:
        medium_layout = f.read() 
        return layout_to_array(medium_layout)

def setup_small():
    with open("Layouts/small.txt", "r") as f:
        small_layout = f.read() 
        return layout_to_array(small_layout)
    

#Breadth-first search to find shortest distance between the given point and every other point in coordinate
def distance_map(grid, start):
    rows, cols = grid.shape

    dist = np.full((rows, cols), -1, dtype=int)

    q = deque([start])
    dist[start] = 0

    directions = [(1,0), (-1,0), (0,1), (0,-1)]

    while q:
        r, c = q.popleft()

        for dr, dc in directions:
            nr, nc = r + dr, c + dc

            if (
                0 <= nr < rows and
                0 <= nc < cols and
                (grid[nr, nc] == "." or grid[nr,nc] == "S") and
                dist[nr, nc] == -1
            ):
                dist[nr, nc] = dist[r, c] + 1
                q.append((nr, nc))
    return dist


# Sets up a dictionary of coordinates (r, c) to an array of distances to other coordinates
def all_distance_maps(grid):
    rows, cols = grid.shape
    result = {}

    for r in range(rows):
        for c in range(cols):
            if grid[r, c] == "." or "S":
                result[(r, c)] = distance_map(grid, (r, c))

    return result


# orders will be a list of coordinates 
def nearest_neighbor(orders, dist_map, staging):

    unvisited = set(orders)

    current = staging

    path = [current]

    while unvisited:
        best_node = None
        best_dist = float("inf")

        for node in unvisited:
            d = dist_map[current][node[0], node[1]]

            # safety check (in case unreachable)
            if d == -1:
                continue

            if d < best_dist:
                best_dist = d
                best_node = node

        if best_node is None:
            break  # remaining nodes unreachable

        path.append(best_node)
        unvisited.remove(best_node)
        current = best_node

    return path

def path_distance(path, dist_map):
    total = 0

    for i in range(len(path) - 1):
        start = path[i]
        end = path[i + 1]

        d = dist_map[start][end[0], end[1]]
        total += d

    return total

small = setup_small()
dist_map = all_distance_maps(small)
coord_map = map_of_coords(small)
staging = coord_map["S"][0]
# connect below orders w/ order generation logic
orders = [
    (1,1),
    (2,2),
    (3,3)
]
route = nearest_neighbor(orders, dist_map, staging)
route.append(staging)
print(route)
print("dist: ", path_distance(route, dist_map))