"""Origin-defined size cohorts; test diagnostics only, no fitting or selection."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd


def score(y,p):
    y,p=np.asarray(y,float),np.asarray(p,float)
    if not np.isfinite(y).all() or not np.isfinite(p).all(): raise ValueError('Missing values')
    return dict(rows=len(y),WAPE=100*float(np.abs(y-p).sum()/y.sum()),BIAS=100*float((p-y).sum()/y.sum())) if y.sum()>0 else dict(rows=len(y),WAPE=None,BIAS=None)


def run(root):
    keys=['institution_code','department','item_code','forecast_origin_month']
    f=pd.read_parquet(root/'.teamlex_git/feature-expansion-20260914/outputs/feature_expansion_v1/test_behavior_predictions.parquet')
    a=pd.read_parquet(root/'outputs/stock_feature_table.parquet',columns=keys[:3]+['year_month','rolling_mean_3','lag_1','standard_item_unit_code'],filters=[('year_month','>=',pd.Timestamp('2025-09-01')),('year_month','<=',pd.Timestamp('2025-11-01'))]).rename(columns={'year_month':'forecast_origin_month'})
    for c in keys[:3]:
        a[c]=a[c].astype(str)
        f[c]=f[c].astype(str)
    f=f.merge(a,on=keys,validate='one_to_one',indicator=True,how='left')
    assert f['_merge'].eq('both').all()
    # Fixed bands, not cutpoints selected to optimize test performance.
    f['past_volume_band']=pd.cut(f.rolling_mean_3,[-np.inf,0,10,100,1000,10000,np.inf],right=True)
    all_error=float((f.prediction-f.target_usage).abs().sum())
    def summarize(g):
        return dict(model=score(g.target_usage,g.prediction),last_month=score(g.target_usage,g.lag_1),mean3=score(g.target_usage,g.rolling_mean_3),
            error_pct=100*float((g.prediction-g.target_usage).abs().sum()/all_error))
    return dict(overall=summarize(f),past_volume={str(k):summarize(g) for k,g in f.groupby('past_volume_band',observed=True)},units={str(k):summarize(g) for k,g in f.groupby('standard_item_unit_code',observed=True)},limitations=['No test-tuned routing or thresholds','Mixed units; bands indicate recorded quantities, not equivalent physical demand','Comparisons on reused test cannot establish future superiority'])


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    result=run(a.source)
    with a.output.open('x',encoding='utf-8') as h: json.dump(result,h,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(result,ensure_ascii=False,indent=2))
