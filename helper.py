# Will include functions for setup of large, medium, and small store layouts

#large function
# what we want to do:


#params:
#numpeople, numrobots, sizeofstore, sizeoforders, numtotalorders, custumor disruption percentage
# constants: freezer perishable time (30m), order due time (4hr), walking speed (1m/s), 
# break time (30 min break in mid of shift), shift schedule (6-2pm, 2-10pm), charging/downtime for AMR, AMR speed 

import numpy as np

categoryMapping = {
    1: "Grocery",
    2: "Perishable Grocery",
    3: "Health & Beauty",
    4: "Misc.",
    5: "Fashion",
    6: "Home",
    7: "Cleaning",
    8: "Toys",
    9: "Pets",
    "A": "Electronics",
    "B": "Garden",
    "C": "Home Improvement",
    "D": "Auto",
    "E": "Sports & Outdoors",
    "F": "Arts & Crafts",
    "S": "Staging"
}

def layout_to_array(layout_text):
    rows = [list(row) for row in layout_text.strip().splitlines()]
    return np.array(rows, dtype=str)

with open("Layouts/large.txt", "r") as f:
    large_layout = f.read() 
    large_layout = layout_to_array(large_layout)

with open("Layouts/medium.txt", "r") as f:
    medium_layout = f.read() 
    medium_layout = layout_to_array(medium_layout)

with open("Layouts/small.txt", "r") as f:
    small_layout = f.read() 
    small_layout = layout_to_array(small_layout)

print("Large Layout: ", large_layout)
print("Medium Layout: ", medium_layout)
print("Small Layout: ", small_layout)

