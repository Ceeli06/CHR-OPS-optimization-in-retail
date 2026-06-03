import numpy as np
import pandas as pd
import scipy.stats as st
from scipy.optimize import minimize

DATA_FILE = "OrderDataset(Mapped).csv"
TARGET_COL = "MappedDepartment"

print("Loading dataset...")
df = pd.read_csv(DATA_FILE, usecols=[TARGET_COL])

print("Calculating department frequencies...")
counts = df[TARGET_COL].dropna().value_counts()
frequencies = counts.values
departments = counts.index.tolist()

if len(frequencies) < 2:
    print("Error: Need at least 2 unique departments to run the fit.")
    exit()

ranks = np.arange(1, len(frequencies) + 1)
idx_array = np.arange(0, len(frequencies))
shares = frequencies / np.sum(frequencies)

def fit_zipf(actual_shares, ranks):
    """Find best alpha parameter for Zipf's Law."""
    def objective(alpha):
        if alpha <= 1: 
            return np.inf
        pred = 1 / (ranks ** alpha)
        pred /= np.sum(pred)
        return np.sum((actual_shares - pred) ** 2)
    
    opt_res = minimize(objective, x0=[1.5], bounds=[(1.001, 10)])
    return opt_res.x[0]

def fit_geometric(actual_shares, ranks):
    """Find best p parameter for Geometric decay."""
    def objective(p):
        if p <= 0 or p >= 1: 
            return np.inf
        pred = st.geom.pmf(ranks, p)
        pred /= np.sum(pred)
        return np.sum((actual_shares - pred) ** 2)
    
    opt_res = minimize(objective, x0=[0.3], bounds=[(0.001, 0.999)])
    return opt_res.x[0]

def fit_poisson(actual_shares, idxs):
    """Find best lambda parameter for Poisson."""
    def objective(lam):
        if lam <= 0: 
            return np.inf
        pred = st.poisson.pmf(idxs, lam)
        if np.sum(pred) == 0: 
            return np.inf
        pred /= np.sum(pred)
        return np.sum((actual_shares - pred) ** 2)
    
    opt_res = minimize(objective, x0=[1.0], bounds=[(0.01, 50.0)])
    return opt_res.x[0]

print("Optimizing curve parameters...")
models = {}

# Zipf curve configuration
alpha_best = fit_zipf(shares, ranks)
zipf_vals = 1 / (ranks ** alpha_best)
models["Zipf"] = {
    "array": zipf_vals / np.sum(zipf_vals),
    "config": f"Zipf(α={alpha_best:.2f})"
}

# Geometric curve configuration
p_best = fit_geometric(shares, ranks)
geom_vals = st.geom.pmf(ranks, p_best)
models["Geometric"] = {
    "array": geom_vals / np.sum(geom_vals),
    "config": f"Geometric(p={p_best:.2f})"
}

# Poisson curve configuration
lam_best = fit_poisson(shares, idx_array)
poisson_vals = st.poisson.pmf(idx_array, lam_best)
models["Poisson"] = {
    "array": poisson_vals / np.sum(poisson_vals),
    "config": f"Poisson(λ={lam_best:.2f})"
}
table_data = []
for idx, name in enumerate(departments):
    actual = shares[idx]
    row_errors = {m_name: abs(actual - m_data["array"][idx]) for m_name, m_data in models.items()}
    winning_model = min(row_errors, key=row_errors.get)
    lowest_error = row_errors[winning_model]
    table_data.append({
        "Rank": idx + 1,
        "Department": name,
        "Actual Count": frequencies[idx],
        "Actual Share": f"{actual:.4f}",
        "Best Model Match": winning_model,
        "Distribution Variables": models[winning_model]["config"],
        "Prediction Error": f"{lowest_error:.5f}"
    })
summary_df = pd.DataFrame(table_data)
print("                    BEST STATISTICAL DISTRIBUTION PER DEPARTMENT")
print("-" * 95)
print(summary_df.to_string(index=False))