import pandas as pd 
for season in [2023, 2024, 2025]: 
    v4 = pd.read_csv(f'bt_{season}_v4.csv') 
    v5 = pd.read_csv(f'bt_{season}_v5.csv') 
    v6 = pd.read_csv(f'bt_{season}_v6.csv') 
    def s(df): return 100 * df['pnl'].sum() / df['stake'].sum() 
    print(f'{season}: v4={s(v4):+6.2f}%%  v5={s(v5):+6.2f}%%  v6={s(v6):+6.2f}%%   n v4/v5/v6 = {len(v4)}/{len(v5)}/{len(v6)}') 
