# Retail DES Framework for the Comparative Analysis of CHROps Policies

A discrete event simulation framework for online order fulfillment that compares six CHROps (Collaborative Human-Mobile Robot Operations) policies: manual, direct follow, deadline aware, zone follow, zone wait, and zone divided within a retail environment. The system models picker and AMR coordination, batching, zoning, and store routing to evaluate performance metrics such as: throughput, order completion times, and order tardiness.

Key features include:
- Multiple policy implementations for direct comparative evaluation
- Realistic order generation driven by cleaned retail transaction data
- Broad range of performance metrics tracking the behavior of human pickers, AMRs, and the overall system
- Configurable experiment sweeps with CSV output for analysis
- Visual analytics dashboard for quick side-by-side policy comparison

## Prerequisites & Requirements

- Python 3.10+
- Third-party packages: `numpy`, `pandas`, `matplotlib`, `tkinter`

## Project Structure

- `models.py` — core simulation objects, event-driven simulation logic, and metrics tracking utilities
- `params.py` — global simulation parameters and constants
- `setup_layout.py` — layout loading, coordinate mapping, distance/routing helpers, and zoning utilities
- `orderGen.py` — helpers for realistic order generation based on cleaned dataset (orders.json)
- `manual.py` — Manual baseline policy 
- `directFollow.py` — Direct Follow AMR policy 
- `deadlineAware.py` — Deadline Aware AMR policy 
- `zoneFollow.py` — Zone Follow AMR picker policy 
- `zoneWait.py` — Zone Wait AMR picker policy 
- `zoneDivided.py` — Zone Divided AMR picker policy 
- `simDashboard.py` — graphical dashboard for running and comparing multiple policies
- `tests/config.py` — experiment configuration for batch runs
- `tests/runner.py` — configuration matrix runner for sensitivity analysis, outputting metric results to a CSV

## How to Run

Choose one of the three execution modes below:

1. Run the simulation dashboard (for single-scenario comparison across policies):
   ```bash
   python simDashboard.py
   ```
   - Opens a GUI to launch and compare policies visually.

2. Run an individual policy script and inspect terminal output (for single-scenario, single policy analysis):
   ```bash
   python manual.py
   python directFollow.py
   python deadlineAware.py
   python zoneFollow.py
   python zoneWait.py
   python zoneDivided.py
   ```

3. Run the batch experiment runner and write results to CSV (for comparisons across multiple policies under multiple scenarios):
   - Edit `tests/config.py` to adjust policies, picker counts, AMR ratios, demand, and customers.
   - Then run:
     ```bash
     python tests/runner.py
     ```
   - Output CSV files are written to `tests/results/`.
