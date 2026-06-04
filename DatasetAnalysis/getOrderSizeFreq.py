import pandas as pd

# Load dataset
df = pd.read_csv("OrderDataset(Mapped).csv")
size_counts = df.groupby("VisitNumber").size().value_counts().sort_index()

# Output dictionary of orderSize : frequencies
print("order_distribution = {")
for size, count in size_counts.items():
    print(f"    {size}: {count},")
print("}")
