import numpy as np
import random

categoryMapping = { #dictionary of item types and their corresponding callsign
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
    "N": "Empty",
    "S": "Staging"
}

#dictionary of order sizes and their frequencies
orderSizeEmpiricalDistribution = {
    1: 19563, 2: 17255, 3: 10362, 4: 8038, 5: 5884, 6: 4633, 7: 3716, 8: 3154, 9: 2593, 10: 2251, 11: 1889, 12: 1746, 13: 1519, 14: 1253, 15: 1207, 16: 1030, 17: 887, 18: 772, 19: 699, 20: 674, 21: 569, 22: 559, 23: 471, 24: 446, 25: 386, 26: 393, 27: 332, 28: 318, 29: 276, 30: 255, 31: 222, 32: 183, 33: 179, 34: 165, 35: 158, 36: 147, 37: 137, 38: 102, 39: 105, 40: 106, 41: 79, 42: 78, 43: 71, 44: 80, 45: 45, 46: 61, 47: 37, 48: 46, 49: 46, 50: 43, 51: 43, 52: 25, 53: 34, 54: 34, 55: 24, 56: 19, 57: 26, 58: 18, 59: 21, 60: 12, 61: 14, 62: 22, 63: 12, 64: 15, 65: 18, 66: 10, 67: 7, 68: 11, 69: 4, 70: 11, 71: 6, 72: 7, 73: 5, 74: 4, 75: 1, 76: 3, 77: 7, 78: 2, 79: 2, 80: 5, 82: 4, 83: 1, 84: 1, 85: 1, 86: 4, 89: 4, 91: 2, 93: 2, 94: 3, 96: 1, 97: 2, 98: 1, 104: 1, 111: 1, 112: 1, 113: 1, 151: 1, 209: 1
}

#def orderGenerator... {generates orders with numpy and order size
#                       from dataset empirical distribution}

#generates order size based on empirical distribution
def sampleOrderSize():
    sizes = list(orderSizeEmpiricalDistribution.keys())
    weights = list(orderSizeEmpiricalDistribution.values())
    return random.choices(sizes, weights=weights, k=1)[0]

#def sampleItemType()... {returns item type based on
#                              fitteddistrobution.py}

#def sampleArrivalTime()... {returns random time 
#                   for order arrival in simulation}
