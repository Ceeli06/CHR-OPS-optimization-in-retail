"""Parameters"""

num_pickers = 4  # default: 4 pickers
num_robots = 4  # default: 4 AMRs
num_customers = 30  # Representative of store congestion (default: 30 customers)
order_arrival_rate = (
    1 / 450
)  # Avg. orders per second for arrivalTimeGen's Poisson process (default:1/450sec{8 orders/hour}))


"""Constants"""
# Perishable items must reach depot within this time or spoil
FREEZER_PERISHABLE_TIME = 60 * 60  # 1 hour
# Orders have between ORDER_DUE_TIME_MIN and ORDER_DUE_TIME_MAX to be fulfilled
ORDER_DUE_TIME_MIN = 2 * 60 * 60  # 2 hours
ORDER_DUE_TIME_MAX = 4 * 60 * 60  # 4 hours
STAGING_TIME = 5 * 60  # 5 minutes
WALKING_SPEED = 1.3  # Walking speed in meters per second
HUMAN_PICK_TIME = 20  # Time to find and manually pick one item at the pick location (includes barcode scanning and other logistical factors)
CART_LOAD_TIME = 0.8  # Seconds to load one item onto the cart/AMR
CART_UNLOAD_TIME = 0.8 # Simulated seconds to unload one item (Reflects reality: unloading totes at 6 items/1 tote/5 seconds)
CART_CAPACITY = 50  # Maximum AMR and manual cart item capacity per trip
AMR_SPEED = 1.5  # AMR speed in meters per second
CUSTOMER_SPEED = 1  # customer speed in meters per second
CUSTOMER_BROWSE_TIME = 30  # Time a customer spends at each item coord browsing
CUSTOMER_COLLISION_TIME = 10  # Time AMR pauses upon colliding with a customer
CUSTOMER_COLLISION_BUFFER = 15  # Seconds of margin added to each side of the AMR/customer occupancy overlap check
BATCH_SIZE_MIN = 4  # Minimum number of orders per batch (group) in batching policies
BATCH_SIZE_MAX = 6  # Maximum number of order per batch (group) in batching policies
SIM_TIME = 8 * 60 * 60  # Simulates 8 hour workday
# Orders waiting longer than this are forceed into the next batch
SIMILARITY_BATCH_MAX_WAIT = 30 * 60  # 30 minutes
# If the pending queue has been non-empty this long without reaching BATCH_SIZE_MIN, force a dispatch
BATCH_TIMEOUT = 5 * 60  # 5 minutes
URGENCY_THRESHOLD = (
    30 * 60
)  # Orders with remaining pick times under this threshold are auto-batched
MANUAL_PUSH_FACTOR = (
    0.5  # Factor that human walking speed is scaled by when pushing a manual cart
)
HUMAN_ADAPTABILITY_FACTOR = 0.5 # Factor that represents how adaptable humans are to disruptions relative to AMRs 
