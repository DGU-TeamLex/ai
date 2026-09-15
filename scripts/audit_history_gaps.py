"""Read-only validation audit of short contiguous histories with prior observations."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd


def classify(f):
    short=f.history_months.lt(6)
    return short,short & f.series_observation_count.gt(f.history_months)


def run(source,paired,output):
    if output.exists():raise FileExistsError(output)
    f=pd.read_parquet(source,columns=['forecast_month','target_usage','history_months','series_observation_count'])
    frames=[];predictions=[]
    for fold,start,end in [('early','2025-05-01','2025-06-01'),('recent','2025-08-01','2025-09-01')]:
        g=f[f.forecast_month.between(start,end)].reset_index(drop=True)
        p=pd.read_parquet(paired/f'{fold}_paired.parquet')
        if len(g)!=len(p) or not np.array_equal(g.target_usage,p.actual) or not np.array_equal(g.forecast_month,p.month):
            raise ValueError('Per-fold order/target mismatch')
        frames.append(g);predictions.append(p)
    f=pd.concat(frames,ignore_index=True);p=pd.concat(predictions,ignore_index=True)
    short,prior=classify(f)
    error=(p.baseline-p.actual).abs()
    result=dict(N=len(f),short=int(short.sum()),short_with_prior_segment=int(prior.sum()),
        short_with_total_observations_ge6=int((short & f.series_observation_count.ge(6)).sum()),
        prior_segment_error_share_pct=float(100*error[prior].sum()/error.sum()),
        prior_segment_perfect_prediction_gain_pp=float(100*error[prior].sum()/f.target_usage.sum()),
        source=str(source.resolve()),paired_source=str(paired.resolve()),
        limitations=['Observation counts identify prior records, not clinically continuous or currently valid histories',
        'No gap filled with zero, no target modified, no new model trained',
        'Same-order validation predictions checked per fold; not independent evaluation'])
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for c in ['source','paired','output']:p.add_argument('--'+c,type=Path,required=True)
    a=p.parse_args();run(a.source,a.paired,a.output)
