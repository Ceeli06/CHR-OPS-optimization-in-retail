import pandas as pd

# Load the data one last time
df = pd.read_csv("OrderDataset(Mapped).csv")
size_counts = df.groupby("VisitNumber").size().value_counts().sort_index()

# Generate copy-pasteable Python code
print("order_distribution = {")
for size, count in size_counts.items():
    print(f"    {size}: {count},")
print("}")