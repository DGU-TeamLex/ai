"""Audit validation errors; realized future states are diagnostics, never features."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd


def states(f, hindsight=False):
    if hindsight:
        # These states use outcomes ONLY to diagnose error; cannot route forecasts.
        prev=f.lag_1.to_numpy(float); now=f.target_usage.to_numpy(float)
        return np.select([(prev==0)&(now>0),(prev>0)&(now==0),
                          (prev>0)&(now>=2*prev),(prev>0)&(now<=.5*prev)],
                         ['restart','stopped','surge','drop'],default='other')
    mean=f.rolling_mean_3.to_numpy(float)
    cv=np.divide(f.rolling_std_3.to_numpy(float),mean,out=np.full(len(f),np.inf),where=mean>0)
    return np.select([f.history_months.to_numpy()<6,f.lag_1.to_numpy()==0,cv<=.2,cv>=.8],
                     ['short_history','last_zero','stable','volatile'],default='moderate')


def metrics(y,p):
    y,p=np.asarray(y,float),np.asarray(p,float)
    if not np.isfinite(y).all() or not np.isfinite(p).all():raise ValueError('Nonfinite value')
    den=y.sum()
    return dict(N=len(y),actual_sum=float(den),absolute_error=float(abs(p-y).sum()),
                WAPE=float(100*abs(p-y).sum()/den) if den>0 else None,
                BIAS=float(100*(p-y).sum()/den) if den>0 else None)


def audit(f):
    overall=metrics(f.target_usage,f.baseline)
    result={'overall':overall}
    axes={'origin_state':states(f),'realized_change_DIAGNOSTIC_ONLY':states(f,True),
          'past_scale':pd.cut(f.rolling_mean_3,[-np.inf,0,100,1000,10000,np.inf],labels=['zero','up_to100','100to1000','1000to10000','over10000']).astype(str)}
    for axis,labels in axes.items():
        result[axis]={}
        for label in sorted(set(labels)):
            g=f.loc[np.asarray(labels)==label]
            row=metrics(g.target_usage,g.baseline)
            row['error_share_pct']=100*row['absolute_error']/overall['absolute_error']
            row['maximum_gain_if_perfect_pp']=100*row['absolute_error']/overall['actual_sum']
            row['mean3']=metrics(g.target_usage,g.rolling_mean_3.clip(lower=0))
            result[axis][label]=row
    return result


def run(a):
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'status.json').write_text('{"stage":"running"}',encoding='utf-8')
    frames=[]
    for fold,start,end in [('early','2025-05-01','2025-06-01'),('recent','2025-08-01','2025-09-01')]:
        f=pd.read_parquet(a.source,columns=['forecast_month','target_usage','lag_1','rolling_mean_3','rolling_std_3','history_months'],
                          filters=[('forecast_month','>=',pd.Timestamp(start)),('forecast_month','<=',pd.Timestamp(end))]).reset_index(drop=True)
        # Trusted ingredient experiment preserved original order and reverified baseline WAPE.
        p=pd.read_parquet(a.paired/f'{fold}_paired.parquet')
        if len(f)!=len(p) or not np.array_equal(f.target_usage.to_numpy(),p.actual.to_numpy()) or not np.array_equal(f.forecast_month.to_numpy(),p.month.to_numpy()):
            raise ValueError('Paired prediction order/target mismatch')
        f['baseline']=p.baseline.to_numpy();f['fold']=fold
        frames.append(f)
    f=pd.concat(frames,ignore_index=True)
    report={'pooled':audit(f),'folds':{k:audit(g) for k,g in f.groupby('fold')},
            'source':str(a.source.resolve()),'paired_source':str(a.paired.resolve()),
            'limitations':['Validation predictions from verified existing 74-feature model, same-order target/month alignment checked',
                'Future restart/surge/drop labels are diagnostic only and cannot be routing rules',
                'These validation periods already used for model development; not independent evaluation',
                'Category thresholds are descriptive hypotheses, not clinical state labels'],
            'new_model_trained':False}
    (a.output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    (a.output/'status.json').write_text('{"stage":"completed"}',encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ['source','paired','output']:p.add_argument('--'+name,type=Path,required=True)
    run(p.parse_args())
