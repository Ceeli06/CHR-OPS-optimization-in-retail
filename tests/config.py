'''
Defines the parameter configurations to be tested by runner.py.
Sets the seeds to be used across runs (Default: 65 seeds starting from 101 to 165)
'''

POLICIES = [
    "Manual",
    "Direct Follow",
    "Deadline Aware",
    "Zone Wait",
    "Zone Follow",
    "Zone Divided",
]

PICKER_COUNTS = [2, 4, 8]

AMR_TO_PICKER_RATIOS = [0.5, 1.0, 1.5, 2.0] # AMRs per picker; AMR count = ratio * picker count

ORDER_DEMANDS_PER_HR = [4, 8, 16, 32, 64] # Average orders per hour

CUSTOMER_COUNTS = [0, 30, 60] 

# Replications per configuration: n = ceil((z * sigma / E)^2) from the pilot
# study at the worst-case matrix corner (32/hr, 2 pickers, ratio 0.5, 60
# customers): z = 1.96, sigma_tardiness = 61.6 min, E = 15 min -> n = 65.
N_SEEDS = 65

# The same seed set is used for every configuration (common random numbers):
# seed i represents "day i", and is simulated under every condition.
SEED_START = 101

# How many experiment results to keep in the results folder
KEEP_RESULTS = 3