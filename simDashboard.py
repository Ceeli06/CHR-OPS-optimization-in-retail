# Main dashboard: runs all 7 picking policies with adjustable parameters,
# compares their metrics, and visualizes a selected policy's routes over the store layout.

import contextlib
import io
import queue
import random
import sys
import threading
import tkinter as tk
from collections import Counter
from tkinter import ttk, scrolledtext

if sys.platform == "win32":
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass

import numpy as np
import matplotlib

matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

import params
import models
from setup_layout import (
    setup_layout,
    map_of_coords,
    all_distance_maps,
    CODE_TO_DEPARTMENT,
)
from orderGen import generate_orders

import manual
import directFollow
import shuttle2Staging
import deadlineAware
import zoneFollow
import zoneWait
import zoneDivided

ORDER_SEED = 12  # Copies fixed seed from orderGen.py and resets for every policy so they use the same order set

POLICIES = [
    {
        "name": "Manual",
        "uses_amr": False,
        "route_method": "build_route_2",
        "build": lambda ctx: manual.ManualSim(
            ctx.orders,
            ctx.pickers,
            ctx.amrs,
            ctx.coord_map,
            staging=ctx.staging,
            dist_map=ctx.dist_map,
            layout=ctx.layout,
        ),
    },
    {
        "name": "Direct Follow",
        "uses_amr": True,
        "route_method": "build_route_2",
        "build": lambda ctx: directFollow.FollowSim(
            ctx.orders,
            ctx.pickers,
            ctx.amrs,
            ctx.coord_map,
            staging=ctx.staging,
            dist_map=ctx.dist_map,
            customers=ctx.customers,
            layout=ctx.layout,
        ),
    },
    {
        "name": "Shuttle to Staging",
        "uses_amr": True,
        "patch_num_zones": True,
        "route_method": "build_decoupled_route",
        "build": lambda ctx: shuttle2Staging.ShuttleSim(
            ctx.orders,
            ctx.pickers,
            ctx.amrs,
            ctx.coord_map,
            ctx.layout,
            staging=ctx.staging,
            dist_map=ctx.dist_map,
            customers=ctx.customers,
        ),
    },
    {
        "name": "Deadline Aware",
        "uses_amr": True,
        "route_method": "build_decoupled_route",
        "build": lambda ctx: deadlineAware.DeadlineSim(
            ctx.orders,
            ctx.pickers,
            ctx.amrs,
            ctx.coord_map,
            staging=ctx.staging,
            dist_map=ctx.dist_map,
            layout=ctx.layout,
            customers=ctx.customers,
        ),
    },
    {
        "name": "Zone Follow",
        "uses_amr": True,
        "needs_dist_map_patch": True,
        "route_method": "build_zoning_route",
        "build": lambda ctx: zoneFollow.ZoneFollow(
            ctx.orders,
            ctx.pickers,
            ctx.amrs,
            ctx.coord_map,
            ctx.layout,
            staging=ctx.staging,
            dist_map=ctx.dist_map,
            customers=ctx.customers,
        ),
    },
    {
        "name": "Zone Wait",
        "uses_amr": True,
        "needs_dist_map_patch": True,
        "route_method": "build_zoning_route",
        "build": lambda ctx: zoneWait.ZoneWait(
            ctx.orders,
            ctx.pickers,
            ctx.amrs,
            ctx.coord_map,
            ctx.layout,
            staging=ctx.staging,
            dist_map=ctx.dist_map,
        ),
    },
    {
        "name": "Zone Divided",
        "uses_amr": True,
        "route_method": "build_zoning_route",
        "build": lambda ctx: zoneDivided.ZoneDivided(
            ctx.orders,
            ctx.pickers,
            ctx.amrs,
            ctx.coord_map,
            ctx.layout,
            staging=ctx.staging,
            dist_map=ctx.dist_map,
        ),
    },
]

PARAM_FIELDS = [
    ("num_pickers", "Pickers", int, 1),
    ("num_robots", "AMRs", int, 0),
    ("num_customers", "Customers", int, 0),
    ("order_arrival_rate", "Order arrival rate (avg. orders/hr)", float, 1e-6),
]

IDENTITY_TRANSFORM = (lambda x: x, lambda x: x)
DISPLAY_TRANSFORMS = {
    "order_arrival_rate": (
        lambda stored: stored * 3600,
        lambda displayed: displayed / 3600,
    ),
}

METRIC_CHARTS = [
    ("avg_completion", "Avg completion time (min)", lambda m: m.avg_completion / 60),
    ("throughput", "Throughput (orders/hr)", lambda m: m.throughput),
    ("late_pct", "Late orders (%)", lambda m: m.late_pct),
    ("avg_picker_idle", "Avg picker idle (min)", lambda m: m.avg_picker_idle / 60),
    ("amr_util", "AMR utilization (%)", lambda m: m.amr_util),
    (
        "avg_picker_distance",
        "Avg picker walk distance (m)",
        lambda m: m.avg_picker_distance,
    ),
]


class RunContext:
    def __init__(
        self, orders, pickers, amrs, customers, coord_map, layout, staging, dist_map
    ):
        self.orders = orders
        self.pickers = pickers
        self.amrs = amrs
        self.customers = customers
        self.coord_map = coord_map
        self.layout = layout
        self.staging = staging
        self.dist_map = dist_map


def instrument_routes(sim, method_name):
    captured = []
    original = getattr(sim, method_name, None)
    if original is None:
        return captured

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.append(result)
        return result

    setattr(sim, method_name, wrapper)
    return captured


# Reconstructs the actual walkable cell-by-cell path from 'start' to 'end' using
# a precomputed BFS distance grid *from* 'start' (dist_map[start], as returned by
# all_distance_maps()), walks backward from 'end', at each step moving to a
# neighbor exactly one step closer to 'start', until 'start' is reached.
def reconstruct_path(dist_from_start, start, end):
    if start == end:
        return [start]

    rows, cols = dist_from_start.shape
    path = [end]
    current = end

    while current != start:
        r, c = current
        d = dist_from_start[r, c]
        next_node = None

        for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and dist_from_start[nr, nc] == d - 1:
                next_node = (nr, nc)
                break

        if next_node is None:
            break  # disconnected; shouldn't happen for reachable waypoints

        path.append(next_node)
        current = next_node

    path.reverse()
    return path


# Expands a sparse waypoint list (ex. a captured picking route) into the full
# sequence of walkable cells actually traversed between each consecutive pair,
# so a plotted route follows real corridors instead of a straight line that can
# cut through non-walkable cells.
def expand_waypoints(waypoints, dist_map):
    if not waypoints:
        return []

    expanded = [waypoints[0]]
    for start, end in zip(waypoints[:-1], waypoints[1:]):
        leg = reconstruct_path(dist_map[start], start, end)
        expanded.extend(leg[1:])

    return expanded


def build_run_context(layout, coord_map, dist_map, staging):
    random.seed(ORDER_SEED)
    raw_orders = generate_orders(
        coord_map, params.SIM_TIME, layout, params.order_arrival_rate
    )
    orders = [
        models.Order(
            id=raw["order_id"],
            arrival_time=raw["arrival_time"],
            due_time=raw["due_time"],
            items=raw["items"],
            coords=raw["coords"],
        )
        for raw in raw_orders
    ]
    pickers = [models.Picker(i, staging) for i in range(params.num_pickers)]
    amrs = [models.AMR(i, staging) for i in range(params.num_robots)]
    customers = [models.Customer(i, staging) for i in range(params.num_customers)]
    return RunContext(
        orders, pickers, amrs, customers, coord_map, layout, staging, dist_map
    )


def run_all_policies():
    # Builds shared layout/distance data, then runs every policy with a
    # fresh, identical seeded order set. Returns a dict keyed by policy name.
    layout = setup_layout()
    coord_map = map_of_coords(layout)
    dist_map = all_distance_maps(layout)
    staging = coord_map["S"][0]
    zoneFollow.dist_map = dist_map
    zoneWait.dist_map = dist_map

    results = {}
    for policy in POLICIES:
        if policy.get("patch_num_zones"):
            shuttle2Staging.ShuttleSim.num_zones = params.num_pickers

        ctx = build_run_context(layout, coord_map, dist_map, staging)
        sim = policy["build"](ctx)
        captured_routes = instrument_routes(sim, policy["route_method"])

        log_buffer = io.StringIO()
        with contextlib.redirect_stdout(log_buffer):
            sim.run()

        results[policy["name"]] = {
            "metrics": sim.metrics,
            "routes": captured_routes,
            "log": log_buffer.getvalue(),
            "handoffPoints": getattr(sim, "handoffPoints", None),
            "zoneMap": getattr(sim, "zoneMap", None),
            "uses_amr": policy["uses_amr"],
        }

    return {
        "layout": layout,
        "coord_map": coord_map,
        "staging": staging,
        "dist_map": dist_map,
        "policies": results,
    }


# Colors a single selected route's line from its first stop (green) to its
# last stop (red), so pick order is readable from color alone.
ROUTE_ORDER_CMAP = LinearSegmentedColormap.from_list(
    "route_order", ["#2ca02c", "#c44e52"]
)

# Route label per policy
ROUTE_LABELS = {
    "Manual": "Picker route",
    "Direct Follow": "Picker + AMR shared route",
    "Shuttle to Staging": "Picker + AMR shared route",
    "Deadline Aware": "Picker + AMR shared route",
    "Zone Follow": "Picker + AMR shared route (per zone)",
    "Zone Wait": "Picker route per zone (AMR shuttles between zone handoff points)",
    "Zone Divided": "Picker route per zone (AMR shuttles between zone handoff points)",
}


class SimDashboard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.tk.call("tk", "scaling", 96 / 72)
        self.title("Retail CHROps DES Dashboard")
        self.geometry("1200x800")

        self.result_queue = queue.Queue()
        self.last_results = None
        self.param_vars = {}

        self._build_param_panel()
        self._build_notebook()

        self.status_var.set("Idle. Adjust parameters and click Re-run All Sims.")

    # UI stuff
    def _build_param_panel(self):
        panel = ttk.Frame(self, padding=8)
        panel.pack(side=tk.TOP, fill=tk.X)

        for key, label, _cast, _minimum in PARAM_FIELDS:
            field = ttk.Frame(panel)
            field.pack(side=tk.LEFT, padx=6)
            ttk.Label(field, text=label).pack(anchor=tk.W)
            to_display, _to_stored = DISPLAY_TRANSFORMS.get(key, IDENTITY_TRANSFORM)
            var = tk.StringVar(value=str(to_display(getattr(params, key))))
            ttk.Entry(field, textvariable=var, width=12).pack()
            self.param_vars[key] = var

        self.run_button = ttk.Button(
            panel, text="Re-run All Sims", command=self.on_run_clicked
        )
        self.run_button.pack(side=tk.LEFT, padx=16)

        self.status_var = tk.StringVar()
        ttk.Label(panel, textvariable=self.status_var).pack(side=tk.LEFT, padx=6)

    def _build_notebook(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.metrics_tab = ttk.Frame(self.notebook)
        self.route_tab = ttk.Frame(self.notebook)
        self.log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.metrics_tab, text="Metrics Comparison")
        self.notebook.add(self.route_tab, text="Route Layout")
        self.notebook.add(self.log_tab, text="Run Log")

        self._build_metrics_tab()
        self._build_route_tab()
        self._build_log_tab()

    def _build_metrics_tab(self):
        self.metrics_fig, self.metrics_axes = plt.subplots(2, 3, figsize=(11, 6.5))
        self.metrics_canvas = FigureCanvasTkAgg(
            self.metrics_fig, master=self.metrics_tab
        )
        self.metrics_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._render_empty_metrics()

    def _build_route_tab(self):
        controls = ttk.Frame(self.route_tab, padding=6)
        controls.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(controls, text="Policy:").pack(side=tk.LEFT)
        self.route_policy_var = tk.StringVar()
        self.route_policy_combo = ttk.Combobox(
            controls, textvariable=self.route_policy_var, state="readonly", width=22
        )
        self.route_policy_combo.pack(side=tk.LEFT, padx=6)
        self.route_policy_combo.bind(
            "<<ComboboxSelected>>", lambda e: self._on_route_policy_change()
        )

        ttk.Label(controls, text="Batch:").pack(side=tk.LEFT, padx=(12, 0))
        self.route_batch_var = tk.StringVar()
        self.route_batch_combo = ttk.Combobox(
            controls, textvariable=self.route_batch_var, state="readonly", width=16
        )
        self.route_batch_combo.pack(side=tk.LEFT, padx=6)
        self.route_batch_combo.bind(
            "<<ComboboxSelected>>", lambda e: self._render_route_tab()
        )

        self.route_fig, self.route_ax = plt.subplots(figsize=(11, 5.5))
        self.route_canvas = FigureCanvasTkAgg(self.route_fig, master=self.route_tab)
        self.route_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._render_empty_route()

    def _build_log_tab(self):
        controls = ttk.Frame(self.log_tab, padding=6)
        controls.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(controls, text="Policy:").pack(side=tk.LEFT)
        self.log_policy_var = tk.StringVar()
        self.log_policy_combo = ttk.Combobox(
            controls, textvariable=self.log_policy_var, state="readonly", width=22
        )
        self.log_policy_combo.pack(side=tk.LEFT, padx=6)
        self.log_policy_combo.bind(
            "<<ComboboxSelected>>", lambda e: self._render_log_tab()
        )

        self.log_text = scrolledtext.ScrolledText(self.log_tab, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # Parameter setting
    def _read_validated_params(self):
        values = {}
        for key, label, cast, minimum in PARAM_FIELDS:
            raw = self.param_vars[key].get().strip()
            try:
                value = cast(raw)
            except ValueError:
                raise ValueError(f"{label} must be a number, got '{raw}'")
            if value < minimum:
                raise ValueError(f"{label} must be >= {minimum}")
            values[key] = value
        return values

    def on_run_clicked(self):
        try:
            values = self._read_validated_params()
        except ValueError as exc:
            self.status_var.set(f"Invalid parameter: {exc}")
            return

        params.num_pickers = values["num_pickers"]
        params.num_robots = values["num_robots"]
        params.num_customers = values["num_customers"]
        _, to_stored = DISPLAY_TRANSFORMS.get("order_arrival_rate", IDENTITY_TRANSFORM)
        params.order_arrival_rate = to_stored(values["order_arrival_rate"])

        self.run_button.config(state=tk.DISABLED)
        self.status_var.set("Running all 7 policies...")

        thread = threading.Thread(target=self._run_in_background, daemon=True)
        thread.start()
        self.after(100, self._poll_run_queue)

    def _run_in_background(self):
        try:
            results = run_all_policies()
            self.result_queue.put(("done", results))
        except Exception as exc:
            self.result_queue.put(("error", str(exc)))

    def _poll_run_queue(self):
        try:
            status, payload = self.result_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_run_queue)
            return

        self.run_button.config(state=tk.NORMAL)
        if status == "error":
            self.status_var.set(f"Run failed: {payload}")
            return

        self.last_results = payload
        self.status_var.set("Done.")
        self._on_results_ready()

    # Rendering stuff
    def _on_results_ready(self):
        policy_names = list(self.last_results["policies"].keys())

        self.route_policy_combo["values"] = policy_names
        if not self.route_policy_var.get():
            self.route_policy_var.set(policy_names[0])

        self.log_policy_combo["values"] = policy_names
        if not self.log_policy_var.get():
            self.log_policy_var.set(policy_names[0])

        self._render_metrics_tab()
        self._on_route_policy_change()
        self._render_log_tab()

    def _render_empty_metrics(self):
        for ax in self.metrics_axes.flat:
            ax.clear()
            ax.set_title("(no data yet)")
        self.metrics_canvas.draw()

    def _render_metrics_tab(self):
        policies = self.last_results["policies"]
        names = list(policies.keys())
        short_names = [n.replace(" ", "\n") for n in names]

        for ax, (key, title, extractor) in zip(self.metrics_axes.flat, METRIC_CHARTS):
            ax.clear()
            values = [extractor(data["metrics"]) for data in policies.values()]
            colors = [
                "#4c72b0" if data["uses_amr"] else "#999999"
                for data in policies.values()
            ]
            ax.bar(short_names, values, color=colors)
            ax.set_title(title, fontsize=9)
            ax.tick_params(axis="x", labelsize=7)
            ax.tick_params(axis="y", labelsize=7)

        self.metrics_fig.tight_layout()
        self.metrics_canvas.draw()

    def _on_route_policy_change(self):
        if not self.last_results:
            return
        policy_name = self.route_policy_var.get()
        data = self.last_results["policies"].get(policy_name)
        if data is None:
            return

        route_count = len(data["routes"])
        options = ["All"] + [f"Batch {i+1}" for i in range(route_count)]
        self.route_batch_combo["values"] = options
        self.route_batch_var.set(options[0])
        self._render_route_tab()

    def _render_empty_route(self):
        self.route_ax.clear()
        self.route_ax.set_title("Run all sims to see route data")
        self.route_canvas.draw()

    def _render_route_tab(self):
        if not self.last_results:
            return
        policy_name = self.route_policy_var.get()
        data = self.last_results["policies"].get(policy_name)
        if data is None:
            return

        layout = self.last_results["layout"]
        staging = self.last_results["staging"]
        dist_map = self.last_results.get("dist_map")
        ax = self.route_ax
        ax.clear()

        self._draw_layout_background(ax, layout, data.get("zoneMap"))

        selection = self.route_batch_var.get()
        routes = data["routes"]
        legend_handles = []
        if selection.startswith("Batch") and routes:
            batch_idx = int(selection.split(" ")[1]) - 1
            legend_handles += self._draw_routes(
                ax,
                [routes[batch_idx]],
                staging,
                alpha=0.9,
                linewidth=2.0,
                dist_map=dist_map,
                order_detail=True,
            )
        else:
            self._draw_routes(
                ax, routes, staging, alpha=0.15, linewidth=1.2, dist_map=dist_map
            )

        handoff_points = data.get("handoffPoints")
        if handoff_points:
            legend_handles.append(self._draw_zone_hop_path(ax, staging, handoff_points))

        if legend_handles:
            ax.legend(handles=legend_handles, loc="upper right", fontsize=7)

        rows, cols = layout.shape
        ax.set_xlim(-1, cols)
        ax.set_ylim(rows, -1)
        ax.set_title(
            f"{policy_name} — {ROUTE_LABELS.get(policy_name, 'Route')}", fontsize=10
        )
        self.route_fig.tight_layout()
        self.route_canvas.draw()

    def _draw_layout_background(self, ax, layout, zone_map):
        rows, cols = layout.shape
        walkable = [
            [0 if layout[r, c] == "." else 1 for c in range(cols)] for r in range(rows)
        ]
        ax.imshow(
            walkable, cmap="Greys", aspect="auto", interpolation="nearest", alpha=0.3
        )

        if zone_map is not None:
            grid = np.array(
                [
                    [
                        (
                            np.nan
                            if (zone_map[r][c] is None or layout[r, c] != ".")
                            else zone_map[r][c]
                        )
                        for c in range(cols)
                    ]
                    for r in range(rows)
                ],
                dtype=float,
            )
            ax.imshow(
                grid, cmap="Pastel1", aspect="auto", interpolation="nearest", alpha=0.6
            )

        self._draw_department_labels(ax, layout)

    def _connected_regions(self, layout):
        # Groups each department's cells into separate 4-connected components,
        # so a department whose cells span multiple disconnected areas of the
        # store (e.g. Fashion appearing in two separate aisle blocks) gets one
        # labeled/outlined region per contiguous block instead of being treated
        # as a single area.
        rows, cols = layout.shape
        visited = set()
        regions = []

        for r in range(rows):
            for c in range(cols):
                code = str(layout[r, c])
                if code == "." or (r, c) in visited:
                    continue

                stack = [(r, c)]
                visited.add((r, c))
                cells = []
                while stack:
                    cr, cc = stack.pop()
                    cells.append((cr, cc))
                    for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                        nr, nc = cr + dr, cc + dc
                        if (
                            0 <= nr < rows
                            and 0 <= nc < cols
                            and (nr, nc) not in visited
                            and str(layout[nr, nc]) == code
                        ):
                            visited.add((nr, nc))
                            stack.append((nr, nc))

                regions.append((code, cells))

        return regions

    def _draw_department_labels(self, ax, layout):
        # Labels each department region with its name, oriented to fit: wide-
        # but-short regions (e.g. the top row) get horizontal text, narrow tall
        # aisles get text rotated to run along their height. Also draws a
        # border around each region so two different departments touching with
        # no walkable gap between them still show a visible boundary.
        for code, cells in self._connected_regions(layout):
            if code == "S":
                continue  # staging is already marked by the route start/AMR-hop markers
            name = CODE_TO_DEPARTMENT.get(code)
            if name is None:
                continue
            rows_list = [r for r, _c in cells]
            cols_list = [c for _r, c in cells]
            min_r, max_r = min(rows_list), max(rows_list)
            min_c, max_c = min(cols_list), max(cols_list)

            ax.add_patch(
                Rectangle(
                    (min_c - 0.5, min_r - 0.5),
                    max_c - min_c + 1,
                    max_r - min_r + 1,
                    fill=False,
                    edgecolor="#555555",
                    linewidth=0.8,
                    zorder=3,
                )
            )

            rotation = 0 if (max_c - min_c) >= (max_r - min_r) else 90
            ax.text(
                (min_c + max_c) / 2,
                (min_r + max_r) / 2,
                name,
                rotation=rotation,
                ha="center",
                va="center",
                fontsize=6.5,
                color="#222222",
                zorder=4,
                bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.55),
            )

    def _draw_routes(
        self, ax, routes, staging, alpha, linewidth, dist_map=None, order_detail=False
    ):
        pick_counts = Counter()
        legend_handles = []
        for route in routes:
            if route is None:
                continue
            paths = route.values() if isinstance(route, dict) else [route]
            for path in paths:
                handles = self._plot_path(
                    ax, path, alpha, linewidth, dist_map, order_detail
                )
                if handles and not legend_handles:
                    legend_handles = handles
                pick_counts.update(coord for coord in path if coord != staging)
        if not order_detail:
            self._draw_pick_markers(ax, pick_counts)
        return legend_handles

    def _plot_path(self, ax, path, alpha, linewidth, dist_map=None, order_detail=False):
        if not path or len(path) < 2:
            return []

        # Walk the actual grid corridors between waypoints instead of a
        # straight line, which can cut through non-walkable department cells.
        walked = expand_waypoints(path, dist_map) if dist_map else path
        cols = [c for _r, c in walked]
        rows = [r for r, _c in walked]

        if not order_detail:
            ax.plot(cols, rows, color="#c44e52", alpha=alpha, linewidth=linewidth)
            return []

        # Color each segment along a green (1st stop) -> red (last stop) gradient
        # and number every stop, so pick order is readable, not just inferred.
        points = np.array([cols, rows]).T.reshape(-1, 1, 2)
        segments = np.concatenate([points[:-1], points[1:]], axis=1)
        seg_colors = ROUTE_ORDER_CMAP(np.linspace(0, 1, len(segments)))
        ax.add_collection(
            LineCollection(
                segments, colors=seg_colors, alpha=alpha, linewidth=linewidth, zorder=5
            )
        )

        # Draw badges from last stop back to first, so when a route loops back
        # through a coordinate it already visited (e.g. start/end at staging),
        # the earlier, more-intuitive number wins visually on top.
        n_stops = len(path)
        for i in reversed(range(n_stops)):
            r, c = path[i]
            stop_color = ROUTE_ORDER_CMAP(i / max(n_stops - 1, 1))
            ax.annotate(
                str(i + 1),
                (c, r),
                fontsize=6,
                color="white",
                ha="center",
                va="center",
                zorder=8,
                bbox=dict(
                    boxstyle="circle,pad=0.18", fc=stop_color, ec="white", lw=0.4
                ),
            )

        return [
            Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                markerfacecolor=ROUTE_ORDER_CMAP(0.0),
                markersize=8,
                label="Pick order: 1st stop",
            ),
            Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                markerfacecolor=ROUTE_ORDER_CMAP(1.0),
                markersize=8,
                label="Pick order: last stop",
            ),
        ]

    def _draw_pick_markers(self, ax, pick_counts):
        # Marks every coordinate where an item was actually picked along the selected route
        if not pick_counts:
            return
        rows = [r for r, _c in pick_counts]
        cols = [c for _r, c in pick_counts]
        sizes = [18 + 14 * count for count in pick_counts.values()]
        ax.scatter(
            cols,
            rows,
            s=sizes,
            color="#1b1b1b",
            alpha=0.85,
            edgecolors="white",
            linewidths=0.5,
            zorder=6,
        )

    def _draw_zone_hop_path(self, ax, staging, handoff_points):
        ordered = (
            [staging]
            + [coord for _zone, coord in sorted(handoff_points.items())]
            + [staging]
        )
        cols = [c for _r, c in ordered]
        rows = [r for r, _c in ordered]
        (line,) = ax.plot(
            cols,
            rows,
            color="#2b8cbe",
            linestyle="--",
            linewidth=1.5,
            alpha=0.7,
            label="Approx. AMR path between zones",
        )
        ax.scatter(cols, rows, color="#2b8cbe", s=25, zorder=5)
        return line

    def _render_log_tab(self):
        if not self.last_results:
            return
        policy_name = self.log_policy_var.get()
        data = self.last_results["policies"].get(policy_name)
        if data is None:
            return
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, data["log"])


if __name__ == "__main__":
    app = SimDashboard()
    app.mainloop()
