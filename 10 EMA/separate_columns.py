import pandas as pd

# Full path (as you prefer)
input_file = r"E:\Projects\Experiments\Strategy Testing\10 EMA\USDJPY_cleaned.csv"
output_file = r"E:\Projects\Experiments\Strategy Testing\10 EMA\USDJPY_with_EMA.csv"

# Load data
df = pd.read_csv(input_file)

# Ensure CLOSE is numeric
df['CLOSE'] = pd.to_numeric(df['CLOSE'], errors='coerce')

# Step 1: Calculate EMA 10
df['EMA_10'] = df['CLOSE'].ewm(span=10, adjust=False).mean()

# Save result
df.to_csv(output_file, index=False)

print("EMA 10 added successfully.")
print(df[['CLOSE', 'EMA_10']].head(15))