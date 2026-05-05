import pandas as pd 
for season in [2023, 2024, 2025]: 
    v4 = pd.read_csv(f'bt_{season}_v4.csv') 
    v5 = pd.read_csv(f'bt_{season}_v5.csv') 
    def s(df): return 100 * df['pnl'].sum() / df['stake'].sum() 
    print(f'{season}: v4 ROI={s(v4):+.2f}%% ({len(v4)} bets, {v4.won.mean()*100:.1f}%% wr) | v5 ROI={s(v5):+.2f}%% ({len(v5)} bets, {v5.won.mean()*100:.1f}%% wr)') 
