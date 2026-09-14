"""Pre-evaluation balanced-panel stability and aggregate forecast feasibility."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

GROUP = ['institution_code', 'department', 'standard_item_family_id', 'standard_item_unit_code']
ITEM = ['institution_code', 'department', 'item_code']


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def valid_metadata(frame):
    values = frame[GROUP].astype('string').fillna('').apply(lambda c:c.str.strip())
    valid = values.ne('').all(axis=1)
    for col in GROUP[2:]:
        valid &= ~values[col].str.contains('UNRESOLVED|UNSPECIFIED|UNKNOWN|MISSING|^NAN$|^NONE$', case=False, regex=True)
    return valid


def balanced_groups(frame, cutoff):
    """Select group membership with only six months available at cutoff."""
    end = pd.Timestamp(cutoff)
    months = pd.date_range(end-pd.DateOffset(months=5),end,freq='MS')
    f = frame[frame.year_month.isin(months)].copy()
    if f.duplicated(ITEM+['year_month']).any():
        raise ValueError('Non-unique item-month')
    # Require all six monthly rows and stable known metadata; no missing->zero.
    eligible = f.groupby(ITEM).agg(n=('year_month','nunique'),valid=('_valid','all'))
    stable = f.groupby(ITEM)[GROUP[2:]].nunique(dropna=False).eq(1).all(axis=1)
    eligible = eligible[eligible.n.eq(6) & eligible.valid & stable].reset_index()[ITEM]
    f = f.merge(eligible,on=ITEM,validate='many_to_one')
    f['_item'] = pd.MultiIndex.from_frame(f[ITEM]).factorize()[0]
    f['_group'] = pd.MultiIndex.from_frame(f[GROUP]).factorize()[0]
    sizes = f.groupby('_group')._item.nunique()
    f = f[f._group.isin(sizes[sizes.ge(2)].index)]
    return f, months


def stability(f, months):
    rows=[]
    for group, g in f.groupby('_group'):
        matrix=g.pivot(index='_item',columns='year_month',values='model_demand_positive_sum').reindex(columns=months).to_numpy(float)
        if not np.isfinite(matrix).all() or (matrix<0).any():
            raise ValueError('Invalid history')
        mean=matrix.mean(axis=1); total=matrix.sum(axis=0)
        if total.mean()<=0:
            continue
        independent_scale=np.std(matrix,axis=1,ddof=1).sum()/mean.sum()
        aggregate_cv=np.std(total,ddof=1)/total.mean()
        # Triangle inequality: reduction is cancellation, not proof of predictability.
        rows.append(dict(group=int(group),items=len(matrix),mean_total=float(total.mean()),
            weighted_item_cv=float(independent_scale),group_cv=float(aggregate_cv),
            cv_reduction_fraction=float(1-aggregate_cv/independent_scale) if independent_scale>0 else 0.))
    return rows


def forecast(history):
    """Rows=items, columns=last 6 calendar months. No target argument."""
    a=np.asarray(history,float)
    if a.ndim!=2 or a.shape[1]!=6 or not np.isfinite(a).all() or (a<0).any():
        raise ValueError('Require complete nonnegative six-month history')
    means=a[:,-3:].mean(axis=1); total=a.sum(axis=0)
    if means.sum()==0:
        return {'item_mean3':means,'group_last':means.copy(),'group_median3':means.copy(),'group_trend':means.copy()}
    shares=means/means.sum()
    return {'item_mean3':means,'group_last':shares*total[-1],
        'group_median3':shares*np.median(total[-3:]),
        'group_trend':shares*max(0,total[-3:].mean()+.5*(total[-3:].mean()-total[:3].mean()))}


def score(y,p):
    y,p=np.asarray(y,float),np.asarray(p,float)
    return dict(N=len(y),absolute_error=float(np.abs(y-p).sum()),actual_sum=float(y.sum()),
        WAPE=float(100*np.abs(y-p).sum()/y.sum()) if y.sum()>0 else None,
        BIAS=float(100*(p-y).sum()/y.sum()) if y.sum()>0 else None)


def evaluate(frame, cutoff, end):
    selected,months=balanced_groups(frame,cutoff)
    stats=stability(selected,months)
    membership=selected[ITEM+GROUP[2:]].drop_duplicates()
    outputs=[]
    skipped=0
    for origin in pd.date_range(cutoff,pd.Timestamp(end)-pd.DateOffset(months=1),freq='MS'):
        pastmonths=pd.date_range(origin-pd.DateOffset(months=5),origin,freq='MS')
        h=frame[frame.year_month.isin(pastmonths)].merge(membership,on=ITEM+GROUP[2:],validate='many_to_one')
        targets=frame[frame.year_month.eq(origin)].merge(membership,on=ITEM+GROUP[2:],validate='one_to_one')
        histories={k:g for k,g in h.groupby(GROUP)}
        target_groups={k:g for k,g in targets.groupby(GROUP)}
        for keys, members in membership.groupby(GROUP):
            hist=histories.get(keys,h.iloc[:0])
            target=target_groups.get(keys,targets.iloc[:0])
            codes=members.item_code.tolist()
            pivot=hist.pivot(index='item_code',columns='year_month',values='model_demand_positive_sum').reindex(index=codes,columns=pastmonths)
            # Missing history or target excludes entire group from diagnostics, counted explicitly.
            target=target.set_index('item_code').reindex(codes)
            if pivot.isna().any().any() or target.target_usage.isna().any():
                skipped+=1
                continue
            preds=forecast(pivot.to_numpy())
            y=target.target_usage.to_numpy(float)
            outputs.append({'origin':str(origin.date()),'scores':{k:score(y,p) for k,p in preds.items()},
                'aggregate_mean_error':float(abs(y.sum()-preds['item_mean3'].sum()))})
    pooled={}
    if outputs:
        for name in outputs[0]['scores']:
            err=sum(r['scores'][name]['absolute_error'] for r in outputs)
            den=sum(r['scores'][name]['actual_sum'] for r in outputs)
            pooled[name]={'WAPE':100*err/den if den else None,'N':sum(r['scores'][name]['N'] for r in outputs)}
    total_mean=sum(r['mean_total'] for r in stats)
    return dict(cutoff=cutoff,groups=len(stats),items=int(membership.shape[0]),
        median_cv_reduction=float(np.median([r['cv_reduction_fraction'] for r in stats])) if stats else None,
        demand_weighted_cv_reduction=sum(r['cv_reduction_fraction']*r['mean_total'] for r in stats)/total_mean if total_mean else None,
        groups_reduction_at_least_20pct=sum(r['cv_reduction_fraction']>=.2 for r in stats),
        skipped_group_origins=skipped,pooled=pooled,
        aggregate_mean_WAPE=100*sum(r['aggregate_mean_error'] for r in outputs)/sum(r['scores']['item_mean3']['actual_sum'] for r in outputs) if outputs and sum(r['scores']['item_mean3']['actual_sum'] for r in outputs)>0 else None)


def run(source,out):
    out.mkdir(parents=True,exist_ok=False)
    save(out/'status.json',{'stage':'running'})
    try:
        cols=list(dict.fromkeys(GROUP+ITEM+['year_month','forecast_month','model_demand_positive_sum','target_usage']))
        f=pd.read_parquet(source,columns=cols,filters=[('year_month','>=',pd.Timestamp('2024-11-01')),('year_month','<=',pd.Timestamp('2025-08-01'))])
        for c in list(dict.fromkeys(GROUP+ITEM)):
            f[c]=f[c].astype('string').fillna('').str.strip()
        f['_valid']=valid_metadata(f)
        if not (f.forecast_month==f.year_month+pd.DateOffset(months=1)).all():
            raise ValueError('Unexpected forecast horizon')
        results={}
        for label,cutoff,end in [('early','2025-04-01','2025-06-01'),('recent','2025-07-01','2025-09-01')]:
            save(out/'status.json',{'stage':'running','fold':label})
            results[label]=evaluate(f,cutoff,end)
        # Early-only candidate selection; recent fold confirms rather than retunes.
        scores=results['early']['pooled']
        selected=min(scores,key=lambda k: scores[k]['WAPE']) if scores else None
        save(out/'results.json',dict(folds=results,selected_on_early=selected,
            source=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            limitations=['Balanced known-metadata subset, not full population performance',
                'Aggregation cancellation is not forecast improvement',
                'Unit labels do not prove clinical substitution or pack-size equivalence',
                'No October-December outcomes inspected; May-September previously used for model development',
                'Simple aggregate forecast screen, not trained aggregate ML or MinT']))
        save(out/'status.json',{'stage':'completed'})
    except Exception as exc:
        save(out/'status.json',{'stage':'failed','error':repr(exc)})
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.source,a.output)
