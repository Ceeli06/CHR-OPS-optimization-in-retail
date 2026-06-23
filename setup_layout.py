# Provides store layout setup, grid-to-coordinate mapping, and routing.

import numpy as np
from collections import defaultdict, deque

CATEGORYMAPPING = {
    "Grocery": 1,
    "Perishable Grocery": 2,
    "Health & Beauty": 3,
    "Miscellaneous": 4,
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
    "Staging": "S",
}


# Convert text-based store layout into a 2D numpy array (each char = one grid cell)
def layout_to_array(layout_text):
    rows = [list(row) for row in layout_text.strip().splitlines()]
    return np.array(rows, dtype=str)


# Build a dict mapping each grid symbol to a list of its (row, col) coordinates
def map_of_coords(layout):
    coord_map = defaultdict(list)
    rows, cols = layout.shape

    for r in range(rows):
        for c in range(cols):
            value = str(layout[r, c])
            if value != ".":  # Skip walking space
                coord_map[value].append((r, c))

    return dict(coord_map)


# Load the layout from file
def setup_layout():
    with open("layout.txt", "r") as f:
        layout = f.read()
        return layout_to_array(layout)


# Use BFS to compute shortest distance from one point to every reachable grid cell
def distance_map(grid, start):
    rows, cols = grid.shape

    dist = np.full((rows, cols), -1, dtype=int)  # -1 = unvisited

    q = deque([start])
    dist[start] = 0

    directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    while q:
        r, c = q.popleft()

        for dr, dc in directions:
            nr, nc = r + dr, c + dc

            if (
                0 <= nr < rows
                and 0 <= nc < cols
                and (grid[nr, nc] == "." or grid[nr, nc] == "S")
                and dist[nr, nc] == -1
            ):
                dist[nr, nc] = dist[r, c] + 1
                q.append((nr, nc))

    return dist


# Precompute BFS distance maps from every grid location to every other location
def all_distance_maps(grid):
    rows, cols = grid.shape
    result = {}

    for r in range(rows):
        for c in range(cols):
            result[(r, c)] = distance_map(grid, (r, c))

    return result


# Build a picking route using a greedy nearest-neighbor heuristic, starting from staging
def nearest_neighbor(orders, dist_map, staging):
    unvisited = set(orders)
    current = staging
    path = [current]

    while unvisited:
        best_node = None
        best_dist = float("inf")

        for node in unvisited:
            d = dist_map[current][node[0], node[1]]

            if d < best_dist and d != -1:
                best_dist = d
                best_node = node

        if best_node is None:
            break

        path.append(best_node)
        unvisited.remove(best_node)
        current = best_node

    return path


# Sum the precomputed distances between consecutive waypoints in a route
def path_distance(path, dist_map):
    total = 0

    for i in range(len(path) - 1):
        start = path[i]
        end = path[i + 1]
        length = dist_map[start][end[0], end[1]]
        if length > 0:
            total += length

    return total


def zone_path_dist(zonePath, dist_map):
    total = 0
    for i in range(len(zonePath)):
        total += path_distance(zonePath[i], dist_map)
    return total


def zone_amr_dist(zonePath, dist_map, staging):
    path = [staging]
    for i in zonePath.keys():
        coord = zonePath[i][0]
        path.append(coord)
    path.append(staging)
    return path_distance(path, dist_map)


def get_path(orders, dist_map, staging, map):
    perishables = []
    non_perishables = []

    for order in orders:
        if order in map["2"]:
            perishables.append(order)
        else:
            non_perishables.append(order)

    front = nearest_neighbor(non_perishables, dist_map, staging)
    end = nearest_neighbor(perishables, dist_map, front[-1])
    totalRoute = front + end[1:]
    totalRoute.append(staging)
    return totalRoute
