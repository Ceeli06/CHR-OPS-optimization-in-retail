'''Parameters'''
num_pickers = 1
num_robots = 10
num_customers = 1  # Representative of store congestion
pLambda = 30.0 # Lambda for arrivalTimeGen's Poisson process (pLambda DOWN == orderChance UP)

'''Constants'''
# Average number of orders per second (avg. 1 order per "pLambda" seconds)
ORDER_ARRIVAL_RATE = 1 / pLambda
# Perishable items must reach depot within this time or spoil
FREEZER_PERISHABLE_TIME = 30 * 60  # 30 minutes
# Orders exceeding this time are marked late
ORDER_DUE_TIME = 4 * 60 * 60  # 4 hours (14400 seconds)
STAGING_TIME = 2 * 60 * 60  # 2 hours
WALKING_SPEED = 1.0  # Walking speed in meters per second
HUMAN_PICK_TIME = 20  # Time to manually pick one item
AMR_LOAD_TIME = 10  # Seconds to load one item
AMR_UNLOAD_TIME = 10  # Seconds to unload one item
AMR_CAPACITY = 10  # Maximum AMR item capacity per trip
AMR_SPEED = 1.5  # AMR speed in meters per second
BATCH_SIZE_MIN = 6 # Minimum number of orders per batch (group) in batching policies
BATCH_SIZE_MAX = 8 # Maximum number of order per batch (group) in batching policies
SIM_TIME = 8 * 60 * 60 # Simulates 8 hour workday