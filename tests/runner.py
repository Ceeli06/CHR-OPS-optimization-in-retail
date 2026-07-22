'''
Sweeps through every configuration in config.py, outputting the metric averages for
each configuration into a CSV file found in the results folder.
'''
import argparse
import contextlib
import csv
import io
import itertools
import os
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

TESTING_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTING_DIR)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, TESTING_DIR)
os.chdir(REPO_ROOT)  

import config
import params
import models
from setup_layout import setup_layout, map_of_coords, all_distance_maps
from orderGen import generate_orders
import manual
import directFollow
import deadlineAware
import zoneFollow
import zoneWait
import zoneDivided

# Maps metrics to unique columns
METRIC_COLUMNS = [
    ("throughput", "throughput (orders/hr)", 1),  # dashboard definition
    ("avg_tardiness", "avg_tardiness (min)", 60),
    ("total_tardy_time", "total_tardy_time (min)", 60),
    ("late_pct", "late_pct (%)", 1),
    ("avg_completion", "avg_completion (min)", 60),
    ("human_distance", "human_distance (m)", 1),  # all pickers
    ("avg_picker_distance", "avg_picker_distance (m)", 1),
    ("human_idle", "human_idle (min)", 60),  # all pickers
    ("avg_picker_idle", "avg_picker_idle (min)", 60),
    ("human_wait_for_amr", "human_wait_for_amr (min)", 60),
    ("amr_wait_for_human", "amr_wait_for_human (min)", 60),
    ("avg_amr_wait_for_human", "avg_amr_wait_for_human (min)", 60),
    ("amr_idle", "amr_idle (min)", 60),  # all AMRs
    ("avg_amr_idle", "avg_amr_idle (min)", 60),
    ("amr_util", "amr_util (%)", 1),
    ("amr_distance", "amr_distance (m)", 1),  # all AMRs
    ("avg_amr_distance", "avg_amr_distance (m)", 1),
    ("avg_exposure", "avg_exposure (min)", 60),
    ("spoiled_pct", "spoiled_pct (%)", 1),
    ("cart_swap_count", "cart_swap_count", 1),
    ("batch_completion_count", "batch_completion_count", 1),
    ("last_batch_size", "last_batch_size (orders)", 1),
    ("total_time_to_finish", "total_time_to_finish (min)", 60),  # last processed event time
    ("total_orders", "total_orders", 1),
]

EXTRA_COLUMNS = ["throughput_eff (orders/hr)", "makespan (min)"]
VALUE_COLUMNS = [header for _, header, _ in METRIC_COLUMNS] + EXTRA_COLUMNS
CONFIG_COLUMNS = ["policy", "pickers", "amr_ratio", "robots", "demand_per_hr", "customers"]

# Maps local policy names to their respective simulation file equivilents
POLICY_BUILDERS = {
    "Manual": lambda o, p, a, c, world: manual.ManualSim(
        o, p, a, world["coord_map"], staging=world["staging"],
        dist_map=world["dist_map"], layout=world["layout"], customers=c
    ),
    "Direct Follow": lambda o, p, a, c, world: directFollow.FollowSim(
        o, p, a, world["coord_map"], staging=world["staging"],
        dist_map=world["dist_map"], customers=c, layout=world["layout"],
    ),
    "Deadline Aware": lambda o, p, a, c, world: deadlineAware.DeadlineSim(
        o, p, a, world["coord_map"], staging=world["staging"],
        dist_map=world["dist_map"], layout=world["layout"], customers=c,
    ),
    "Zone Wait": lambda o, p, a, c, world: zoneWait.ZoneWait(
        o, p, a, world["coord_map"], world["layout"], staging=world["staging"],
        dist_map=world["dist_map"], customers=c,
    ),
    "Zone Follow": lambda o, p, a, c, world: zoneFollow.ZoneFollow(
        o, p, a, world["coord_map"], world["layout"], staging=world["staging"],
        dist_map=world["dist_map"], customers=c,
    ),
    "Zone Divided": lambda o, p, a, c, world: zoneDivided.ZoneDivided(
        o, p, a, world["coord_map"], world["layout"], staging=world["staging"],
        dist_map=world["dist_map"], customers=c,
    ),
}

_WORLD = None  # per-process cache of layout/coord_map/dist_map


def get_world():
    '''
    Caches spatial enviroment data (ex. distance map, coordmap, etc.) so
    it is not recalculated on every run.
    '''
    global _WORLD
    if _WORLD is None:
        layout = setup_layout()
        coord_map = map_of_coords(layout)
        dist_map = all_distance_maps(layout)
        _WORLD = {
            "layout": layout,
            "coord_map": coord_map,
            "dist_map": dist_map,
            "staging": coord_map["S"][0],
        }
        # Mirror simDashboard.run_all_policies' module-level patch
        zoneFollow.dist_map = dist_map
        zoneWait.dist_map = dist_map
    return _WORLD


def run_one(cfg, seed, world):
    '''
    Run one policy under one config with one seed, returning a metrics row.
    '''
    params.num_pickers = cfg["pickers"]
    params.num_robots = cfg["robots"]
    params.num_customers = cfg["customers"]
    params.order_arrival_rate = cfg["demand_per_hr"] / 3600.0

    # Same seed -> identical order stream for every policy at this demand level
    random.seed(seed)
    raw_orders = generate_orders(
        world["coord_map"], params.SIM_TIME, world["layout"], params.order_arrival_rate
    )
    orders = [
        models.Order(
            id=r["order_id"],
            arrival_time=r["arrival_time"],
            due_time=r["due_time"],
            items=r["items"],
            coords=r["coords"],
            is_perishable=any(
                "perishable" in str(i.get("department", "")).lower()
                for i in r["items"]
            ),
        )
        for r in raw_orders
    ]
    staging = world["staging"]
    pickers = [models.Picker(i, staging) for i in range(params.num_pickers)]
    amrs = [models.AMR(i, staging) for i in range(params.num_robots)]
    customers = [models.Customer(i, staging) for i in range(params.num_customers)]

    sim = POLICY_BUILDERS[cfg["policy"]](orders, pickers, amrs, customers, world)
    with contextlib.redirect_stdout(io.StringIO()):
        sim.run()

    # Extract metrics and change into proper units
    m = sim.metrics
    row = dict(cfg)
    row["seed"] = seed
    for attr, header, divisor in METRIC_COLUMNS:
        row[header] = round(getattr(m, attr) / divisor, 3)

    # Compute makespan, which is immune to trailing customer events
    completion_times = [
        o.at_staging_time + params.STAGING_TIME
        for o in sim.orders
        if o.at_staging_time is not None
    ]
    makespan = max(completion_times) if completion_times else 0.0
    denom_hr = max(params.SIM_TIME, makespan) / 3600.0
    row["makespan (min)"] = round(makespan / 60, 3)
    row["throughput_eff (orders/hr)"] = round(
        m.total_orders / denom_hr if denom_hr > 0 else 0.0, 3
    )
    return row


def run_config(cfg, seeds):
    '''
    Worker task: run all seeds for one config; returns list of raw rows.
    '''
    world = get_world()
    return [run_one(cfg, seed, world) for seed in seeds]


def build_matrix(args):
    '''
    Generates the full Cartesian product of configuration parameters to test.
    '''
    configs = []
    for policy, pickers, ratio, demand, customers in itertools.product(
        args.policies, args.pickers, args.ratios, args.demands, args.customers
    ):
        configs.append(
            {
                "policy": policy,
                "pickers": pickers,
                "amr_ratio": ratio,
                "robots": int(round(ratio * pickers)),
                "demand_per_hr": demand,
                "customers": customers,
            }
        )
    return configs


def summarize(raw_rows, n_seeds):
    '''
    Groups individual runs by configuration and computes sample statistics 
    (mean and standard deviation) for each metric column.
    '''
    cells = {}
    for row in raw_rows:
        key = tuple(row[c] for c in CONFIG_COLUMNS)
        cells.setdefault(key, []).append(row)

    summary_rows = []
    for key in sorted(cells, key=lambda k: str(k)):
        runs = cells[key]
        out = dict(zip(CONFIG_COLUMNS, key))
        out["n"] = len(runs)
        for col in VALUE_COLUMNS:
            vals = [r[col] for r in runs]
            out[f"mean_{col}"] = round(statistics.mean(vals), 3)
            out[f"std_{col}"] = round(
                statistics.stdev(vals) if len(vals) > 1 else 0.0, 3
            )
        summary_rows.append(out)
    return summary_rows


def build_run_name(args):
    '''
    Build a descriptive result-file name with date, scope, and n.
    '''
    stamp = time.strftime("%Y-%m-%d_%H%M")
    if args.tag:
        scope = args.tag
    else:
        is_full = (
            sorted(args.policies) == sorted(config.POLICIES)
            and args.pickers == config.PICKER_COUNTS
            and args.ratios == [float(r) for r in config.AMR_TO_PICKER_RATIOS]
            and args.demands == [float(d) for d in config.ORDER_DEMANDS_PER_HR]
            and args.customers == config.CUSTOMER_COUNTS
        )
        scope = "full-matrix" if is_full else "subset"
    return f"{stamp}_{scope}_n{args.n}"


def prune_results(out_dir, keep):
    '''
    Prune all but the newest `keep` results files.
    '''
    runs = {}
    for name in os.listdir(out_dir):
        if name.endswith(".csv") and name.startswith(("raw_", "summary_")):
            run_key = name.split("_", 1)[1]
            runs.setdefault(run_key, []).append(os.path.join(out_dir, name))

    ordered = sorted(
        runs.values(),
        key=lambda paths: max(os.path.getmtime(p) for p in paths),
        reverse=True,
    )
    for paths in ordered[keep:]:
        for path in paths:
            os.remove(path)
            print(f"Pruned old result: {os.path.basename(path)}")


def parse_int_list(text):
    return [int(x) for x in text.split(",")]


def parse_float_list(text):
    return [float(x) for x in text.split(",")]


def main():
    parser = argparse.ArgumentParser(description="Run the DES testing matrix.")
    parser.add_argument("--n", type=int, default=config.N_SEEDS,
                        help=f"replications (seeds) per config (default {config.N_SEEDS})")
    parser.add_argument("--seed-start", type=int, default=config.SEED_START)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--tag", default=None,
                        help="optional label for output filenames; defaults to "
                             "'full-matrix' or 'subset' (date and n are always included)")
    parser.add_argument("--policies", type=lambda s: s.split(","), default=config.POLICIES)
    parser.add_argument("--pickers", type=parse_int_list, default=config.PICKER_COUNTS)
    parser.add_argument("--ratios", type=parse_float_list, default=config.AMR_TO_PICKER_RATIOS)
    parser.add_argument("--demands", type=parse_float_list, default=config.ORDER_DEMANDS_PER_HR)
    parser.add_argument("--customers", type=parse_int_list, default=config.CUSTOMER_COUNTS)
    args = parser.parse_args()

    for name in args.policies:
        if name not in POLICY_BUILDERS:
            parser.error(f"unknown policy '{name}' (valid: {list(POLICY_BUILDERS)})")

    seeds = list(range(args.seed_start, args.seed_start + args.n))
    configs = build_matrix(args)
    total_runs = len(configs) * len(seeds)
    print(
        f"{len(configs)} configs x {len(seeds)} seeds = {total_runs} runs "
        f"on {args.workers} workers"
    )

    out_dir = os.path.join(TESTING_DIR, "results")
    os.makedirs(out_dir, exist_ok=True)
    run_name = build_run_name(args)
    raw_path = os.path.join(out_dir, f"raw_{run_name}.csv")
    summary_path = os.path.join(out_dir, f"summary_{run_name}.csv")

    raw_rows = []
    start = time.time()

    # Execute runs as a pool to maximize multi-core CPU usage
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_config, cfg, seeds): cfg for cfg in configs}
        done = 0
        for future in as_completed(futures):
            raw_rows.extend(future.result())
            done += 1
            if done % 25 == 0 or done == len(configs):
                elapsed = time.time() - start
                rate = done / elapsed
                eta = (len(configs) - done) / rate if rate > 0 else 0
                print(
                    f"  {done}/{len(configs)} configs "
                    f"({elapsed:.0f}s elapsed, ~{eta:.0f}s left)"
                )

    # Export individual run results
    raw_fields = CONFIG_COLUMNS + ["seed"] + VALUE_COLUMNS
    with open(raw_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=raw_fields)
        writer.writeheader()
        writer.writerows(raw_rows)

    # Calculate and export experiment statistics
    summary_rows = summarize(raw_rows, len(seeds))
    with open(summary_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"\nWrote {raw_path} ({len(raw_rows)} rows)")
    print(f"Wrote {summary_path} ({len(summary_rows)} config cells)")
    prune_results(out_dir, config.KEEP_RESULTS)
    print(f"Total time: {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
