"""Draw the paired AP-count and BSSID-overlap figure for the project report."""

import csv
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

ROOT = Path(__file__).resolve().parent
with (ROOT/'paired_drift.csv').open(newline='', encoding='utf-8') as stream:
    rows = list(csv.DictReader(stream))

fig, (counts, overlap) = plt.subplots(1,2,figsize=(10,7.3),sharey=True,layout='constrained',
                                     gridspec_kw={'width_ratios':[1.45,1]})
for y,row in enumerate(rows):
    first,last = (float(row[key]) for key in ('d1_median_scan_aps','d2_median_scan_aps'))
    counts.plot([first,last],[y,y],color='#aab7c5',linewidth=1.4,zorder=1)
    focus = row['point_id'] in {'T8-F5-F-TR01','T8-F6-C-TR01'}
    overlap.barh(y,float(row['bssid_jaccard']),height=.58,color='#167a6f' if focus else '#aab7c5')
    if focus:
        j=float(row['bssid_jaccard'])
        overlap.text(j+.008,y,f'{j:.1%}',va='center',fontsize=9,color='#165b54')
y = list(range(len(rows)))
counts.scatter([float(r['d1_median_scan_aps']) for r in rows],y,s=24,label='D1 · Nov 8–9',color='#2b5291',zorder=2)
counts.scatter([float(r['d2_median_scan_aps']) for r in rows],y,s=24,label='Revisit · Nov 11',color='#0e8473',zorder=2)
counts.set_yticks(y,[f"{r['floor']}F-{r['anchor_zone']}" for r in rows])
counts.invert_yaxis()
counts.set_xlim(left=0)
counts.set(title='AP counts by date',xlabel='Median observed BSSIDs per scan')
overlap.set(title='BSSID overlap across dates',xlabel='Jaccard overlap (shared / union)')
overlap.set_xlim(0,max(float(r['bssid_jaccard']) for r in rows)+.08)
overlap.xaxis.set_major_formatter(PercentFormatter(1))
counts.legend(loc='lower right',frameon=True,facecolor='white',edgecolor='none',fontsize=9)
for panel in (counts,overlap):
    for boundary in range(4,len(rows),4):
        panel.axhline(boundary-.5,color='#e8edf1',linewidth=.8,zorder=0)
    panel.grid(axis='x',color='#e8edf1',linewidth=.8)
    panel.set_axisbelow(True)
    panel.spines[['top','right','left']].set_visible(False)
    panel.tick_params(axis='y',length=0)
fig.suptitle('Temporal robustness · 20 matched north-facing training locations',fontsize=14,weight='bold')
fig.savefig(ROOT/'temporal_drift.png',dpi=180)
