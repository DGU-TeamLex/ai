"""Learn group totals, allocate to items, and score the unchanged item population."""
import argparse
import gc
import json
import pickle
from pathlib import Path
import numpy as np
import pandas as pd

GROUP = ['institution_code','department','standard_item_family_id','standard_item_unit_code']
SUM = ['lag_1','lag_2','lag_3','lag_6','rolling_mean_3','rolling_mean_6',
       'inbound_qty_lag_1','month_end_stock_lag_1','outbound_last7','outbound_last14']
FEATURES = GROUP + SUM + ['member_count','month','year','recent_change','mean_member_cv']


def save(p,obj):
    temp=p.with_suffix('.tmp')
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temp.replace(p)


def score(y,p):
    y,p=np.asarray(y,float),np.asarray(p,float)
    if y.shape!=p.shape or not np.isfinite(y).all() or not np.isfinite(p).all():raise ValueError('Invalid scores')
    den=y.sum()
    return dict(N=len(y),WAPE=float(100*abs(p-y).sum()/den) if den>0 else None,
                BIAS=float(100*(p-y).sum()/den) if den>0 else None)


def group_frame(f):
    """Current-origin membership only. Invalid history excludes whole current group."""
    keys=f[GROUP].astype('string').fillna('').apply(lambda s:s.str.strip())
    valid=keys.ne('').all(axis=1)
    for c in GROUP[2:]:
        valid &= ~keys[c].str.contains('UNKNOWN|UNRESOLVED|UNSPECIFIED|MISSING|^NONE$|^NAN$',case=False,regex=True)
    work=f.copy()
    for c in GROUP:work[c]=keys[c]
    work['_history_ok']=work.history_months.ge(6) & work[SUM].notna().all(axis=1)
    work['_cv']=(work.rolling_std_3/work.rolling_mean_3.replace(0,np.nan)).fillna(0)
    key=GROUP+['forecast_month']
    if work.duplicated(['institution_code','department','item_code','forecast_month']).any():raise ValueError('Duplicate item-month')
    work=work.loc[valid].copy()
    agg=work.groupby(key,observed=True,sort=False)
    totals=agg[SUM+['target_usage']].sum(min_count=1)
    totals['member_count']=agg.size()
    totals['history_ok']=agg._history_ok.all()
    totals['mean_member_cv']=agg._cv.mean()
    totals['historical_training_eligible']=agg.historical_training_eligible.all()
    totals=totals.loc[totals.member_count.ge(2)&totals.history_ok].reset_index()
    totals['month']=totals.forecast_month.dt.month
    totals['year']=totals.forecast_month.dt.year
    totals['recent_change']=totals.lag_1-totals.rolling_mean_3
    totals['group_id']=np.arange(len(totals))
    mapping=work.reset_index(names='row_id').merge(totals[key+['group_id']],on=key,validate='many_to_one')
    return totals,mapping[['row_id','group_id']]


def encode(frame,bundle):
    x=frame[bundle['columns']].copy()
    for c,cats in bundle['categories'].items():x[c]=pd.Categorical(x[c].astype('string').fillna('__MISSING__'),categories=cats)
    for c,median in bundle['medians'].items():x[c]=x[c].replace([np.inf,-np.inf],np.nan).fillna(median).astype('float32')
    return x


def allocation(base,anchor,ids,totals,method):
    weights=np.maximum(np.asarray(base if method=='model' else anchor,float),0)
    frame=pd.DataFrame(dict(g=ids,w=weights))
    sums=frame.groupby('g').w.transform('sum').to_numpy()
    counts=frame.groupby('g').w.transform('size').to_numpy()
    shares=np.divide(weights,sums,out=1/counts,where=sums>0)
    return shares*np.asarray(totals,float)


def fit_group(total,cutoff,start,end,out,label):
    import lightgbm as lgb
    hist=total.forecast_month.between('2018-02-01','2020-01-01') & total.historical_training_eligible
    train=total.forecast_month.le(cutoff)&(hist|total.forecast_month.ge('2024-02-01'))
    valid=total.forecast_month.between(start,end)
    bundle=dict(columns=FEATURES,categories={},medians={})
    for c in FEATURES:
        if c in GROUP:bundle['categories'][c]=total.loc[train,c].astype('string').fillna('__MISSING__').unique().tolist()
        else:bundle['medians'][c]=float(total.loc[train,c].replace([np.inf,-np.inf],np.nan).median())
    model=lgb.LGBMRegressor(objective='regression_l1',n_estimators=1999,num_leaves=31,
        min_child_samples=20,learning_rate=.03,n_jobs=2,random_state=42,force_col_wise=True,
        histogram_pool_size=64,verbosity=-1)
    model.fit(encode(total.loc[train],bundle),total.loc[train,'target_usage'],
        eval_set=[(encode(total.loc[valid],bundle),total.loc[valid,'target_usage'])],
        callbacks=[lgb.early_stopping(100,verbose=False),lgb.log_evaluation(100)])
    bundle['model']=model
    with (out/f'{label}_group.pkl').open('wb') as h:pickle.dump(bundle,h)
    save(out/f'{label}_fit.json',dict(train_groups=int(train.sum()),valid_groups=int(valid.sum()),best_iteration=model.best_iteration_))
    return bundle


def evaluate(source,total,start,end,basepath,groupmodel):
    with basepath.open('rb') as h:base=pickle.load(h)
    cols=list(dict.fromkeys(base['columns']+SUM+GROUP+['item_code','year_month','forecast_month','target_usage','historical_training_eligible']))
    f=pd.read_parquet(source,columns=cols,filters=[('forecast_month','>=',pd.Timestamp(start)),('forecast_month','<=',pd.Timestamp(end))]).reset_index(drop=True)
    pred=np.maximum(base['model'].predict(encode(f,base),num_threads=2),0)
    groups,mapping=group_frame(f)
    gp=np.maximum(groupmodel['model'].predict(encode(groups,groupmodel),num_threads=2),0)
    candidates={'direct':pred}
    ids=mapping.row_id.to_numpy()
    gid=mapping.group_id.to_numpy()
    for method in ['model','history']:
        allocated=allocation(pred[ids],f.loc[ids,'rolling_mean_3'].to_numpy(),gid,gp[gid],method)
        for w in [.25,.5,1.]:
            p=pred.copy();p[ids]=(1-w)*p[ids]+w*allocated
            candidates[f'{method}_{w}']=p
    eligible=np.zeros(len(f),bool);eligible[ids]=True
    summed=pd.DataFrame(dict(group=gid,p=pred[ids])).groupby('group').p.sum().reindex(groups.group_id).to_numpy()
    diagnostic=dict(eligible_rows=int(eligible.sum()),group_count=len(groups),
                    total_direct=score(groups.target_usage,summed),total_learned=score(groups.target_usage,gp))
    return f,candidates,eligible,diagnostic


def reports(f,variants,eligible):
    return {k:dict(overall=score(f.target_usage,p),linked_group=score(f.target_usage[eligible],p[eligible]),
        monthly={str(m.date()):score(g.target_usage,p[g.index]) for m,g in f.groupby('forecast_month')}) for k,p in variants.items()}


def run(a):
    a.output.mkdir(parents=True,exist_ok=False)
    try:
        save(a.output/'status.json',dict(stage='prepare'))
        save(a.output/'plan.json',dict(group_keys=GROUP,features=FEATURES,weights=[0,.25,.5,1],
            early_selection='May-June',confirmation='August-September',test='reused October-December',
            limitations=['25.51% was aggregate cancellation on a subset, not item accuracy',
            'Current observed-member totals, not fixed institution-wide baskets; panel availability may bias results',
            'Unit labels do not establish clinical substitution or pack-size equivalence',
            'Existing evaluation periods reused; no independent confirmation'],serving_changed=False))
        cols=list(dict.fromkeys(GROUP+SUM+['forecast_month','item_code','history_months','rolling_std_3','target_usage','historical_training_eligible']))
        f=pd.read_parquet(a.source/'prepared.parquet',columns=cols,filters=[('forecast_month','<=',pd.Timestamp('2025-09-01'))])
        total,_=group_frame(f)
        del f
        gc.collect()
        save(a.output/'status.json',dict(stage='fit_early',group_rows=len(total)))
        early=fit_group(total,'2025-04-01','2025-05-01','2025-06-01',a.output,'early')
        f,variants,eligible,diag=evaluate(a.source/'prepared.parquet',total,'2025-05-01','2025-06-01',a.source/'early_direct_full.pkl',early)
        early_report=reports(f,variants,eligible)
        selected=min(variants,key=lambda k:early_report[k]['overall']['WAPE'])
        save(a.output/'early.json',dict(scores=early_report,diagnostic=diag,selected=selected))
        del f,variants,early
        gc.collect()
        save(a.output/'status.json',dict(stage='fit_recent',selected_on_early=selected))
        recent=fit_group(total,'2025-07-01','2025-08-01','2025-09-01',a.output,'recent')
        f,variants,eligible,diag=evaluate(a.source/'prepared.parquet',total,'2025-08-01','2025-09-01',a.source/'recent_direct_full.pkl',recent)
        report=reports(f,{k:variants[k] for k in dict.fromkeys(['direct',selected])},eligible)
        confirmed=selected if report[selected]['overall']['WAPE']<report['direct']['overall']['WAPE'] else 'direct'
        save(a.output/'selection.json',dict(selected_on_early=selected,confirmed=confirmed,scores=report,diagnostic=diag))
        del f,variants
        gc.collect()
        save(a.output/'status.json',dict(stage='evaluate_reused_test'))
        f,variants,eligible,diag=evaluate(a.source/'prepared.parquet',total,'2025-10-01','2025-12-01',a.source/'recent_direct_full.pkl',recent)
        kept={k:variants[k] for k in dict.fromkeys(['direct',selected])}
        results=dict(selected_on_early=selected,confirmed=confirmed,test=reports(f,kept,eligible),diagnostic=diag,
                     improvement_pp=score(f.target_usage,variants['direct'])['WAPE']-score(f.target_usage,variants[selected])['WAPE'])
        f[['institution_code','department','item_code','year_month','forecast_month','target_usage']].assign(direct=variants['direct'],challenger=variants[selected],eligible_group=eligible).to_parquet(a.output/'predictions.parquet',index=False)
        save(a.output/'results.json',results)
        save(a.output/'status.json',dict(stage='completed',improvement_pp=results['improvement_pp']))
    except Exception as e:
        save(a.output/'status.json',dict(stage='failed',error=repr(e)))
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    run(p.parse_args())
