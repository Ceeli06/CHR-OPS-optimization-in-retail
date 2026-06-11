import pandas as pd
import json

df = pd.read_csv("OrderDataset(Mapped).csv")

# remove invalid scans
df = df[df["ScanCount"] > 0]

orders = {}

for visit_id, group in df.groupby("VisitNumber"):
    
    dept_totals = group.groupby("MappedDepartment")["ScanCount"].sum()

    orders[str(visit_id)] = [
        {
            "department": dept,
            "quantity": int(qty)
        }
        for dept, qty in dept_totals.items()
    ]

with open("orders.json", "w") as f:
    json.dump(orders, f)