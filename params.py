#simulation parameters
num_pickers = 1
num_robots = 1
num_customers = 1 #representative of overall store congestion

#simulation constants
FREEZER_PERISHABLE_TIME = 30 * 60 #30 minute time limit from pick to staging for perishable grocery items before they are considered spoiled
ORDER_DUE_TIME = 4 * 60 * 60 # 4 hour time limit from order generation to completion before it is considered late
STAGING_TIME = 2 * 60 * 60 # Simulation assumes staging is a flat 2 hour process
WALKING_SPEED = 1.0 # human walking speed = 1 meter per second
HUMAN_PICK_TIME = 20 # seconds to pick an item by a human = 20 seconds
AMR_LOAD_TIME = 10 # seconds to load an item onto the AMR = 10 seconds
AMR_UNLOAD_TIME = 10 # seconds to unload an item from the AMR = 10 seconds
AMR_CAPACITY = 10 # maximum number of items an AMR can carry = 10 items
AMR_SPEED = 1.5 # AMR movement speed = 1.5 meters per second
ORDER_ARRIVAL_RATE = 1 / 300.0 # average number of orders per second (1/lambda)
BATCH_SIZE_MIN = 6 
BATCH_SIZE_MAX = 8
SIM_TIME = 8 * 60 * 60 # Simulates 8 hour workday