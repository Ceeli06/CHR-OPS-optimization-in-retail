"""Parameters"""

num_pickers = 1
num_robots = 3
num_customers = 100  # Representative of store congestion
order_arrival_rate = (
    1 / 300.0
)  # Avg. orders per second for arrivalTimeGen's Poisson process (default:1/300sec{1/5min or 12/hr}))

"""Constants"""
# Perishable items must reach depot within this time or spoil
FREEZER_PERISHABLE_TIME = 30 * 60  # 30 minutes
# Orders exceeding this time are marked late
ORDER_DUE_TIME = 4 * 60 * 60  # 4 hours (14400 seconds)
STAGING_TIME = 2 * 60 * 60  # 2 hours
WALKING_SPEED = 1.0  # Walking speed in meters per second
HUMAN_PICK_TIME = 10  # Time to manually pick one item
AMR_LOAD_TIME = 10  # Seconds to load one item
AMR_UNLOAD_TIME = 10  # Seconds to unload one item
AMR_CAPACITY = 50  # Maximum AMR item capacity per trip
AMR_SPEED = 1.5  # AMR speed in meters per second
CUSTOMER_SPEED = 0.8  # customer speed in meters per second
CUSTOMER_BROWSE_TIME = 15  # Time a customer spends at each item coord browsing
CUSTOMER_COLLISION_TIME = 10  # Time AMR pauses upon colliding with a customer
CUSTOMER_COLLISION_BUFFER = 15  # Seconds of margin added to each side of the AMR/customer occupancy overlap check
BATCH_SIZE_MIN = 6  # Minimum number of orders per batch (group) in batching policies
BATCH_SIZE_MAX = 8  # Maximum number of order per batch (group) in batching policies
SIM_TIME = 8 * 60 * 60  # Simulates 8 hour workday
# Orders waiting longer than this are forceed into the next batch
SIMILARITY_BATCH_MAX_WAIT = 90 * 60  # 1.5 hours
# If the pending queue has been non-empty this long without reaching BATCH_SIZE_MIN, force a dispatch
BATCH_TIMEOUT = 5 * 60  # 5 minutes
URGENCY_THRESHOLD = (
    30 * 60
)  # Orders with remaining pick times under this threshold are auto-batched
MANUAL_PUSH_FACTOR = (
    0.8  # Factor that human walking speed is scaled by when pushing a manual cart
)
BATCH_TIMEOUT = 5 * 60  # 5 minutes
