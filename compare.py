import pandas as pd 
for f in ['bt_2023_v4.csv','bt_2024_v4.csv','bt_2025_v4.csv']: 
    df = pd.read_csv(f) 
    roi = 100*df['pnl'].sum()/df['stake'].sum() 
    print(f'{f}: n={len(df)}, win={df["won"].mean():.3f}, pnl={df["pnl"].sum():.2f}, stake={df["stake"].sum():.2f}, roi={roi:.2f}%%') 
    print(df.groupby('tier').agg(n=('won','size'),wr=('won','mean'),pnl=('pnl','sum'),stake=('stake','sum')).round(3)) 
    print() 
