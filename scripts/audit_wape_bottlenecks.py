"""Read saved predictions; no fitting, model changes, or external data upload."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def run(root):
    path = root/'.teamlex_git/model-family-search-20260913/outputs/family_search_v1'
    ref = pd.read_parquet(path/'evaluation.parquet')
    cols = ['year_month','institution_code','department','item_code','history_months',
            'lag_1','rolling_mean_3','zero_rate_6','standard_item_unit_code',
            'standard_item_family_id','standardization_confidence','stockout_rate_lag_1']
    f = pd.read_parquet(root/'outputs/stock_feature_table.parquet',columns=cols,
        filters=[('year_month','>=',pd.Timestamp('2025-07-01')),('year_month','<=',pd.Timestamp('2025-11-01'))])
    keys = ['forecast_origin_month','institution_code','department','item_code']
    f = f.rename(columns={'year_month':'forecast_origin_month'})
    for c in keys[1:]:
        f[c]=f[c].astype(str)
        ref[c]=ref[c].astype(str)
    ref=ref.merge(f,on=keys,how='left',validate='one_to_one',indicator=True)
    assert ref['_merge'].eq('both').all()
    f=ref[ref.forecast_month.ge('2025-10-01')].copy()
    y=f.target_usage.to_numpy(float)
    p=f.stock_model_a_usage_only_pred.to_numpy(float)
    e=np.abs(p-y)
    def subset(mask):
        mask=np.asarray(mask)
        return dict(rows=int(mask.sum()),row_share=100*float(mask.mean()),
            error_share=100*float(e[mask].sum()/e.sum()),
            wape_contribution_pp=100*float(e[mask].sum()/y.sum()),
            actual_share=100*float(y[mask].sum()/y.sum()))
    out=dict(test_rows=len(f),WAPE=100*e.sum()/y.sum(),BIAS=100*(p-y).sum()/y.sum(),
        groups={
            'actual_zero':subset(y==0), 'actual_positive':subset(y>0),
            'underforecast':subset(p<y),'overforecast':subset(p>y),
            'origin_history_under_6':subset(f.history_months.lt(6)),
            'origin_zero_rate_at_least_half':subset(f.zero_rate_6.ge(.5)),
            'expost_usage_over_twice_recent_mean':subset((y>2*f.rolling_mean_3)&(f.rolling_mean_3>0)),
            'origin_stockout_indicator_positive':subset(f.stockout_rate_lag_1.gt(0))},
        error_concentration={}, history_quantiles=f.history_months.quantile([0,.25,.5,.75,1]).to_dict(),
        stockout_indicator_nonmissing=int(f.stockout_rate_lag_1.notna().sum()),
        unit_distinct=int(f.standard_item_unit_code.nunique()))
    for fraction in [.01,.05,.1]:
        count=int(np.ceil(len(f)*fraction))
        out['error_concentration'][str(fraction)]=100*float(np.sort(e)[-count:].sum()/e.sum())
    # Aggregate profile without exposing institution/item identifiers.
    f['abs_error']=e
    g=f.groupby(['institution_code','department','item_code'],observed=True).abs_error.sum().sort_values(ascending=False)
    out['series_count']=len(g)
    out['top_5pct_series_error_share']=100*float(g.iloc[:int(np.ceil(len(g)*.05))].sum()/g.sum())
    out['baseline_reference_monthly']={str(m.date()):dict(WAPE=100*float((g.stock_model_a_usage_only_pred-g.target_usage).abs().sum()/g.target_usage.sum())) for m,g in f.groupby('forecast_month')}
    # Fit only a scalar mixture on the predefined validation period, no test selection.
    valid=ref.forecast_month.lt('2025-10-01').to_numpy()
    target=ref.target_usage.to_numpy(float)
    base=ref.stock_model_a_usage_only_pred.to_numpy(float)
    combined=[]
    for name in ['nhits','timesfm','patchtst','chronos2']:
        arr=np.load(path/f'{name}_predictions.npy',mmap_mode='r')
        # Merge preserves left row order; explicit row_id lookup protects alignment.
        alt=arr[ref.row_id.to_numpy(int)]
        weights=np.linspace(0,1,21)
        errors=[np.abs((1-w)*base[valid]+w*alt[valid]-target[valid]).sum() for w in weights]
        w=float(weights[int(np.argmin(errors))])
        pred=(1-w)*base+w*alt
        combined.append(dict(alternative=name,validation_selected_weight=w,
            validation_WAPE=100*float(np.abs(pred[valid]-target[valid]).sum()/target[valid].sum()),
            test_WAPE=100*float(np.abs(pred[~valid]-target[~valid]).sum()/target[~valid].sum())))
    out['simple_mixtures']=combined
    assert np.isclose(out['groups']['actual_zero']['error_share'] + out['groups']['actual_positive']['error_share'],100)
    assert np.isclose(out['groups']['underforecast']['wape_contribution_pp'] - out['groups']['overforecast']['wape_contribution_pp'],-out['BIAS'])
    assert all(0 <= row['validation_selected_weight'] <= 1 for row in combined)
    out['limitations']=['Post-hoc diagnostics of reused test; not predictive features or independent validation',
        'Simple mixtures screened on Aug-Sep validation, not selected using test',
        'History is contiguous segment history, not total institution history',
        'Quantity units are not converted by this audit']
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    result=run(args.source)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as handle:
        json.dump(result,handle,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
