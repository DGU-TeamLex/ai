"""Frozen-model routing and historical-share reconciliation; no test tuning."""
import argparse
import hashlib
import json
import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def metrics(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    if y.shape != p.shape or not np.isfinite(y).all() or not np.isfinite(p).all() or y.sum() <= 0:
        raise ValueError('Invalid evaluation')
    return dict(N=len(y), WAPE=float(100*np.abs(y-p).sum()/y.sum()), BIAS=float(100*(p-y).sum()/y.sum()))


def segments(f):
    mean = f.rolling_mean_3.clip(lower=0)
    cv = f.rolling_std_3 / mean.replace(0, np.nan)
    size = pd.cut(mean, [-np.inf, 0, 100, 1000, 10000, np.inf], labels=False).fillna(-1)
    variability = pd.cut(cv, [-np.inf, .2, .8, np.inf], labels=False).fillna(-1)
    return size.astype(str)+'|'+variability.astype(str)+'|'+f.history_months.ge(6).astype(str)


def learn_weights(f, base):
    anchor = f.rolling_mean_3.fillna(0).clip(lower=0).to_numpy()
    y = f.target_usage.to_numpy()
    def best(mask):
        return min([0., .25, .5, .75, 1.], key=lambda w: np.abs(y[mask]-((1-w)*base[mask]+w*anchor[mask])).sum())
    weights = {'default': best(np.ones(len(f), bool))}
    labels = segments(f)
    for label in labels.unique():
        mask = labels.eq(label).to_numpy()
        if mask.sum() >= 500 and y[mask].sum() > 0:
            weights[label] = best(mask)
    return weights


def route(f, base, weights):
    w = segments(f).map(weights).fillna(weights['default']).to_numpy(float)
    return (1-w)*base+w*f.rolling_mean_3.fillna(0).clip(lower=0).to_numpy()


def reconcile(f, base):
    """Preserve summed base forecast; redistribute using origin-only mean shares."""
    cols = ['institution_code','department','standard_item_family_id','standard_item_unit_code']
    keys = f[cols].astype('string').fillna('')
    eligible = keys.ne('').all(axis=1)
    for c in cols[2:]:
        eligible &= ~keys[c].str.contains('UNRESOLVED|UNSPECIFIED|UNKNOWN|__MISSING__', case=False, regex=True)
    tmp = keys.copy()
    tmp['month'] = f.forecast_month.to_numpy()
    # Ineligible rows cannot share a group with another row.
    tmp['isolation'] = np.where(eligible, '', np.arange(len(f)).astype(str))
    tmp['base'] = base
    tmp['anchor'] = f.rolling_mean_3.fillna(0).clip(lower=0).to_numpy()
    group = tmp.groupby(cols+['month','isolation'], dropna=False)
    totals = group[['base','anchor']].transform('sum')
    count = group['base'].transform('size')
    use = eligible & count.ge(2) & totals.anchor.gt(0)
    result = np.asarray(base, float).copy()
    result[use] = (totals.base*tmp.anchor/totals.anchor.replace(0,np.nan))[use]
    return result, int(use.sum())


def load_frame(source, start, end, bundle):
    cols = list(dict.fromkeys(bundle['columns']+['year_month','forecast_month','item_code','target_usage']))
    f = pd.read_parquet(source/'prepared.parquet', columns=cols,
        filters=[('forecast_month','>=',pd.Timestamp(start)),('forecast_month','<=',pd.Timestamp(end))]).reset_index(drop=True)
    x = f[bundle['columns']].copy()
    for c, cats in bundle['categories'].items():
        x[c] = pd.Categorical(x[c].astype('string').fillna('__MISSING__'), categories=cats)
    for c, median in bundle['medians'].items():
        x[c] = x[c].replace([np.inf,-np.inf],np.nan).fillna(median).astype('float32')
    pred = np.maximum(0, bundle['model'].predict(x, num_threads=2))
    return f, pred


def candidates(f, base, weights):
    routed = route(f, base, weights)
    grouped, n = reconcile(f, base)
    combined, _ = reconcile(f, routed)
    return {'direct':base, 'routing':routed, 'group_share':grouped, 'routing_group_share':combined}, n


def run(source, out):
    out.mkdir(parents=True, exist_ok=False)
    write(out/'status.json', {'stage':'running'})
    try:
        hashes = {name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ['early_direct_full.pkl','recent_direct_full.pkl']}
        write(out/'manifest.json', {'source':str(source), 'model_sha256':hashes, 'threads':2,
            'fit_weights':'2025-05..06', 'select_method':'2025-08..09', 'exploratory_test':'2025-10..12',
            'limitations':['Saved base models early-stopped on their respective validation folds; not nested unbiased OOF',
                'Test period reused; no independent confirmation', 'Historical-share redistribution is not MinT or a trained aggregate model',
                'Known unit label does not prove pack-size equivalence or clinical substitutability',
                'Unavailable metadata falls back to direct predictions; population never excluded']})
        with (source/'early_direct_full.pkl').open('rb') as h:
            b = pickle.load(h)
        f, p = load_frame(source,'2025-05-01','2025-06-01',b)
        weights = learn_weights(f,p)
        write(out/'weights.json',weights)
        del f, p, b
        with (source/'recent_direct_full.pkl').open('rb') as h:
            b = pickle.load(h)
        f, p = load_frame(source,'2025-08-01','2025-09-01',b)
        variants, eligible = candidates(f,p,weights)
        validation = {k:metrics(f.target_usage,v) for k,v in variants.items()}
        winner = min(validation, key=lambda k:validation[k]['WAPE'])
        write(out/'selection.json', {'winner':winner,'validation':validation,'group_eligible_rows':eligible})
        del f, p, variants
        f, p = load_frame(source,'2025-10-01','2025-12-01',b)
        # Selection frozen; never compare losing candidates on test.
        if winner == 'direct':
            selected = p
        elif winner == 'routing':
            selected = route(f,p,weights)
        else:
            selected, _ = reconcile(f, route(f,p,weights) if winner.startswith('routing') else p)
        results = {}
        for name, pred in {'direct':p, winner:selected}.items():
            results[name] = {'overall':metrics(f.target_usage,pred), 'monthly':{
                str(month.date()):metrics(f.loc[mask,'target_usage'],pred[mask])
                for month in f.forecast_month.unique() for mask in [f.forecast_month.eq(month).to_numpy()]}}
        write(out/'test_results.json',results)
        keys = ['year_month','forecast_month','institution_code','department','item_code','target_usage']
        f[keys].assign(direct=p, selected=selected).to_parquet(out/'predictions.parquet', index=False)
        write(out/'status.json', {'stage':'completed','winner':winner})
    except Exception as exc:
        write(out/'status.json', {'stage':'failed','error':repr(exc)})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    run(args.source,args.output)
