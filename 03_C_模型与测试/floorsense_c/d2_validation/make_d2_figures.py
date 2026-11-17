"""Render comparison of held-out scan confusion matrices from the saved CSVs."""
import os
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR','/tmp/floorsense-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

stage = Path(__file__).resolve().parent
fig, axes = plt.subplots(2,2,figsize=(10.3,8.1),layout='constrained')
for r,(name,title) in enumerate([('D1_RandomForest','Frozen D1 Random Forest'),('stable_ap_balanced_rf','D1-trained balanced RF')]):
    for c,target in enumerate(('floor','zone')):
        ax = axes[r,c]
        cm = pd.read_csv(stage/'comparison'/f'cm_scan_{name}_{target}.csv',index_col=0)
        counts = cm.to_numpy()
        ratios = counts / counts.sum(axis=1,keepdims=True)
        ax.imshow(ratios,cmap='Blues',vmin=0,vmax=1)
        ax.set_xticks(range(len(cm.columns)),cm.columns)
        ax.set_yticks(range(len(cm.index)),cm.index)
        ax.set(title=f'{title} · {target.capitalize()}',xlabel='Predicted',ylabel='Actual')
        for (i,j),number in cm.stack().items():
            ri,ci = cm.index.get_loc(i),cm.columns.get_loc(j)
            rate=ratios[ri,ci]
            ax.text(ci,ri,f'{number}\n{rate:.0%}',ha='center',va='center',color='white' if rate>.53 else '#233347')
fig.suptitle('D2 static validation · 89 deduplicated scans across 15 points',fontsize=15)
fig.savefig(stage/'D2_扫描混淆矩阵.png',dpi=165)
