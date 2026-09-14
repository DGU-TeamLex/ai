"""Paired full-data ingredient ablation; undated metadata is exploratory only."""
import argparse
import gc
import json
import pickle
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import pandas as pd

EXTRA = ['linked_ingredient_code', 'linked_usage_division', 'linked_drug_kind', 'linked_drug_unit']


def write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def metric(y, p):
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    if y.shape != p.shape or not np.isfinite(y).all() or not np.isfinite(p).all() or y.sum() <= 0:
        raise ValueError('Invalid metric input')
    return dict(N=len(y), actual_sum=float(y.sum()), absolute_error=float(abs(p-y).sum()),
                WAPE=float(100*abs(p-y).sum()/y.sum()), BIAS=float(100*(p-y).sum()/y.sum()))


def transform(frame, bundle):
    x = frame[bundle['columns']].copy()
    for c, cats in bundle['categories'].items():
        x[c] = pd.Categorical(x[c].astype('string').fillna('__MISSING__'), categories=cats)
    for c, median in bundle['medians'].items():
        x[c] = x[c].replace([np.inf, -np.inf], np.nan).fillna(median).astype('float32')
    return x


def masks(f, cutoff, start, end):
    history = f.year_month.between('2018-01-01', '2019-12-01') & f.historical_training_eligible.fillna(False)
    return (f.forecast_month.le(cutoff) & (history | f.year_month.ge('2024-01-01')),
            f.forecast_month.between(start, end))


def fit(a):
    import lightgbm as lgb
    manifest = json.loads((a.baseline/'manifest.json').read_text(encoding='utf-8'))
    with (a.baseline/f'{a.fold}_direct_full.pkl').open('rb') as handle:
        base = pickle.load(handle)  # Trusted locally produced model only.
    cols = base['columns'] + EXTRA
    f = pd.read_parquet(a.input, columns=cols+['year_month','forecast_month','target_usage','historical_training_eligible'])
    train, valid = masks(f, *manifest['folds'][a.fold])
    x, v = f.loc[train,cols].copy(), f.loc[valid,cols].copy()
    y, vy = f.loc[train,'target_usage'].to_numpy(), f.loc[valid,'target_usage'].to_numpy()
    months = f.loc[valid,'forecast_month'].copy()
    linked = v.linked_ingredient_code.notna().to_numpy()
    bp = np.maximum(base['model'].predict(transform(v, base)), 0)
    expected = json.loads((a.baseline/f'{a.fold}_direct_full.json').read_text())['validation']
    if not np.isclose(metric(vy,bp)['WAPE'], expected['WAPE'], atol=1e-8, rtol=0):
        raise ValueError('Saved baseline does not reproduce original validation')
    del f, base
    gc.collect()
    bundle = dict(columns=cols, categories={}, medians={})
    for c in cols:
        if isinstance(x[c].dtype, pd.CategoricalDtype) or not pd.api.types.is_numeric_dtype(x[c]):
            values = x[c].astype('string').fillna('__MISSING__')
            bundle['categories'][c] = values.unique().tolist()
        else:
            values = x[c].replace([np.inf,-np.inf],np.nan)
            bundle['medians'][c] = float(values.median()) if values.notna().any() else 0.
    tx = transform(x,bundle)
    del x
    gc.collect()
    tv = transform(v,bundle)
    model = lgb.LGBMRegressor(**dict(manifest['params'], metric='None'))
    def clipped_mae(labels, pred):
        return 'original_quantity_mae', float(abs(np.maximum(pred,0)-labels).mean()), False
    model.fit(tx,y,sample_weight=np.ones(len(y)),eval_set=[(tv,vy)],eval_metric=clipped_mae,
              callbacks=[lgb.early_stopping(100,verbose=False),lgb.log_evaluation(100)])
    bundle['model'] = model
    pred = np.maximum(model.predict(tv),0)
    with (a.output/f'{a.fold}_ingredient.pkl').open('wb') as handle:
        pickle.dump(bundle,handle)
    rows = pd.DataFrame(dict(month=months.to_numpy(),actual=vy,baseline=bp,ingredient=pred,linked=linked))
    rows.to_parquet(a.output/f'{a.fold}_paired.parquet',index=False)
    report = dict(train_rows=len(y), best_iteration=model.best_iteration_, baseline_reproduced=True,
                  overall={k:metric(vy,p) for k,p in [('baseline',bp),('ingredient',pred)]},
                  monthly={str(m.date()):{k:metric(g.actual,g[k]) for k in ['baseline','ingredient']} for m,g in rows.groupby('month')},
                  subgroups={str(flag):{k:metric(g.actual,g[k]) for k in ['baseline','ingredient']} for flag,g in rows.groupby('linked')})
    write(a.output/f'{a.fold}.json',report)


def finalize(a):
    rows = pd.concat([pd.read_parquet(a.output/f'{fold}_paired.parquet') for fold in ['early','recent']],ignore_index=True)
    scores = {k:metric(rows.actual,rows[k]) for k in ['baseline','ingredient']}
    # Fixed single challenger. Selection is locked before loading reused Oct-Dec outcomes.
    winner = min(scores,key=lambda k:scores[k]['WAPE'])
    write(a.output/'selection.json',dict(winner=winner,validation=scores))
    monthly = np.array([[g.actual.sum(),abs(g.baseline-g.actual).sum(),abs(g.ingredient-g.actual).sum()] for _,g in rows.groupby('month')])
    rng = np.random.default_rng(42)
    sums = monthly[rng.integers(0,len(monthly),size=(2000,len(monthly)))].sum(axis=1)
    deltas = 100*(sums[:,1]-sums[:,2])/sums[:,0]
    result = dict(validation=scores, improvement_pp=scores['baseline']['WAPE']-scores['ingredient']['WAPE'],
        month_bootstrap_95_interval=np.quantile(deltas,[.025,.975]).tolist(), bootstrap_positive_fraction=float((deltas>0).mean()),
        limitations=['Only four validation months; bootstrap uncertainty is weakly identified',
        'Undated ingredient snapshot; historical availability unverified', 'Prior evaluation periods reused; exploratory comparison only'])
    f = pd.read_parquet(a.input,filters=[('forecast_month','>=',pd.Timestamp('2025-10-01')),('forecast_month','<=',pd.Timestamp('2025-12-01'))])
    for name,path in [('baseline',a.baseline/'recent_direct_full.pkl'),('ingredient',a.output/'recent_ingredient.pkl')]:
        with path.open('rb') as handle: bundle=pickle.load(handle)
        f[name]=np.maximum(bundle['model'].predict(transform(f,bundle)),0)
    result['reused_test']={k:metric(f.target_usage,f[k]) for k in ['baseline','ingredient']}
    result['reused_test_monthly']={str(m.date()):{k:metric(g.target_usage,g[k]) for k in ['baseline','ingredient']} for m,g in f.groupby('forecast_month')}
    result['reused_test_subgroups']={str(flag):{k:metric(g.target_usage,g[k]) for k in ['baseline','ingredient']} for flag,g in f.groupby(f.linked_ingredient_code.notna())}
    f[['institution_code','department','item_code','year_month','forecast_month','target_usage','baseline','ingredient','linked_ingredient_code']].to_parquet(a.output/'test_paired.parquet',index=False)
    write(a.output/'results.json',result)


def suite(a):
    import psutil
    a.output.mkdir(parents=True,exist_ok=False)
    write(a.output/'plan.json',dict(features=EXTRA,baseline=str(a.baseline.resolve()),input=str(a.input.resolve()),
        selection='Pooled May-June and August-September validation WAPE',candidate_columns_used=False,
        temporal_validity_verified=False,serving_changed=False))
    for action,fold in [('fit','early'),('fit','recent'),('finalize','recent')]:
        label=f'{action}_{fold}'
        write(a.output/'status.json',dict(stage='running',task=label))
        cmd=[sys.executable,'-u',str(Path(__file__).resolve()),action,'--fold',fold,'--input',str(a.input),'--baseline',str(a.baseline),'--output',str(a.output)]
        with (a.output/f'{label}.log').open('w',encoding='utf-8') as log:
            p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
            while p.poll() is None:
                if psutil.virtual_memory().available < 1.5*1024**3:
                    p.terminate();p.wait()
                    write(a.output/'status.json',dict(stage='paused',task=label,reason='low_memory'))
                    return
                time.sleep(5)
        if p.returncode:
            write(a.output/'status.json',dict(stage='failed',task=label,returncode=p.returncode));return
        time.sleep(15)
    write(a.output/'status.json',dict(stage='completed'))


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['suite','fit','finalize'])
    p.add_argument('--fold',choices=['early','recent'],default='early')
    for name in ['input','baseline','output']:p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    globals()[a.action](a)
