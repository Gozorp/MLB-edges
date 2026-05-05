import pandas as pd 
df = pd.read_csv('bt_f5s_2025.csv') 
print(df['side'].value_counts()) 
print() 
print('Win rate by side:') 
print(df.groupby('side')['won'].agg(['count','mean','sum'])) 
