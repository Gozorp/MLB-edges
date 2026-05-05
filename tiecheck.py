import pandas as pd 
from datetime import date 
from mlb_edge import data_ingestion as di 
sc = di.fetch_statcast_range(date(2025,3,20), date(2025,10,5)) 
sc['game_date'] = pd.to_datetime(sc['game_date']) 
f5 = sc[sc['inning'] <= 5] 
agg = f5.groupby('game_pk').agg(h=('post_home_score','max'), a=('post_away_score','max')).reset_index() 
total = len(agg) 
tied = (agg['h'] == agg['a']).sum() 
home_leads = (agg['h'] > agg['a']).sum() 
away_leads = (agg['h'] < agg['a']).sum() 
print(f'Total F5 outcomes: {total}') 
print(f'F5 tied:       {tied} ({100*tied/total:.1f}%%)') 
print(f'F5 home leads: {home_leads} ({100*home_leads/total:.1f}%%)') 
print(f'F5 away leads: {away_leads} ({100*away_leads/total:.1f}%%)') 
