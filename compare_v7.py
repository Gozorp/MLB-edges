"""Compare v6 (shrinkage + calibration) vs v7 (+ catcher framing).

v5 -> v6: shrinkage toward league mean for early-season batting/bullpen rates;
          Stage 2 isotonic calibration.
v6 -> v7: real catcher-framing CSAE (pp) replacing dead 1.0 constants in the
          home_catcher_penalty / away_catcher_penalty columns.
"""
import pandas as pd

rows_hdr = (
    f"{'season':<8}"
    f"{'v6 ROI':>12}{'v6 bets':>10}{'v6 wr':>9}"
    f"{'v7 ROI':>12}{'v7 bets':>10}{'v7 wr':>9}"
    f"{'delta':>10}"
)
print(rows_hdr)
print('-' * 90)
for season in [2023, 2024, 2025]:
    try:
        v6 = pd.read_csv(f'bt_{season}_v6.csv')
        v7 = pd.read_csv(f'bt_{season}_v7.csv')
    except FileNotFoundError as e:
        print(f'{season}: missing file -> {e.filename}')
        continue

    def s(df):
        stake = df['stake'].sum()
        return 100 * df['pnl'].sum() / stake if stake else float('nan')

    def wr(df):
        return df['won'].mean() * 100 if len(df) else float('nan')

    r6, r7 = s(v6), s(v7)
    print(
        f'{season:<8}'
        f'{r6:+11.2f}%{len(v6):>10}{wr(v6):>8.1f}%'
        f'{r7:+11.2f}%{len(v7):>10}{wr(v7):>8.1f}%'
        f'{r7-r6:+9.2f}pp'
    )
