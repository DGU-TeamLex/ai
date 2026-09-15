"""Frozen-baseline challenge with sklearn HGB, explicit temporal validation."""
import argparse
import gc
import hashlib
import json
import pickle
from pathlib import Path
import subprocess
import sys
import time
import numpy as np
import pandas as pd


def save(path,obj):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');temp.replace(path)


def metric(y,p):
    y,p=np.asarray(y,float),np.asarray(p,float)
    if y.shape!=p.shape or not np.isfinite(y).all() or not np.isfinite(p).all() or y.sum()<=0:raise ValueError('Invalid metric')
    return dict(N=len(y),absolute_error=float(abs(y-p).sum()),actual_sum=float(y.sum()),
                WAPE=float(100*abs(y-p).sum()/y.sum()),BIAS=float(100*(p-y).sum()/y.sum()))


def scale(f,arm):
    return np.maximum(f.rolling_mean_3.fillna(0).to_numpy(float),1) if arm=='scaled' else np.ones(len(f))


def fit_encoder(f,cols):
    cats={};freq={}
    for c in cols:
        if isinstance(f[c].dtype,pd.CategoricalDtype) or not pd.api.types.is_numeric_dtype(f[c]):
            counts=f[c].astype('string').value_counts()
            cats[c]=counts.index[:253].tolist()
            freq[c]=(counts/len(f)).to_dict()
    return dict(columns=cols,categories=cats,frequencies=freq)


def encode(f,b):
    data={};categorical=[]
    for c in b['columns']:
        if c in b['categories']:
            v=f[c].astype('string')
            mapping={s:i for i,s in enumerate(b['categories'][c])}
            data[c]=v.map(mapping).fillna(253).to_numpy(float)
            data[c][v.isna().to_numpy()]=np.nan
            categorical.append(c)
            data[c+'__frequency']=v.map(b['frequencies'][c]).fillna(0).to_numpy(float)
        else:data[c]=pd.to_numeric(f[c],errors='coerce').replace([np.inf,-np.inf],np.nan).to_numpy(float)
    return pd.DataFrame(data,index=f.index),categorical


def fit(a):
    from sklearn.ensemble import HistGradientBoostingRegressor
    manifest=json.loads((a.source/'manifest.json').read_text(encoding='utf-8'))
    cols=manifest['features']
    f=pd.read_parquet(a.source/'prepared.parquet',columns=cols+['year_month','forecast_month','target_usage','historical_training_eligible'],
        filters=[('forecast_month','<=',pd.Timestamp(manifest['folds'][a.fold][2]))])
    cutoff,start,end=manifest['folds'][a.fold]
    history=f.year_month.between('2018-01-01','2019-12-01') & f.historical_training_eligible.fillna(False)
    tr=f.forecast_month.le(cutoff)&(history|f.year_month.ge('2024-01-01'))
    va=f.forecast_month.between(start,end)
    train=f.loc[tr,cols];valid=f.loc[va,cols]
    y=f.loc[tr,'target_usage'].to_numpy(float);vy=f.loc[va,'target_usage'].to_numpy(float)
    months=f.loc[va,'forecast_month'].to_numpy()
    ts=scale(train,a.arm);vs=scale(valid,a.arm)
    bundle=fit_encoder(train,cols)
    x,cat=encode(train,bundle);v,_=encode(valid,bundle)
    del f,train,valid
    gc.collect()
    model=HistGradientBoostingRegressor(loss='absolute_error',learning_rate=.05,max_iter=1200,
        max_leaf_nodes=63,min_samples_leaf=50,l2_regularization=1.,max_bins=255,
        categorical_features=[c in cat for c in x.columns],early_stopping=True,scoring='loss',
        n_iter_no_change=75,tol=1e-7,random_state=42,verbose=1)
    # s*abs(y/s-p)=abs(y-s*p), for each nonnegative prediction p. Preserve original-unit L1.
    model.fit(x,y/ts,sample_weight=ts,X_val=v,y_val=vy/vs,sample_weight_val=vs)
    pred=np.maximum(model.predict(v)*vs,0)
    bundle.update(model=model,arm=a.arm)
    stem=f'{a.fold}_{a.arm}'
    with (a.output/f'{stem}.pkl').open('wb') as h:pickle.dump(bundle,h)
    pd.DataFrame(dict(month=months,actual=vy,prediction=pred)).to_parquet(a.output/f'{stem}_predictions.parquet',index=False)
    save(a.output/f'{stem}.json',dict(validation=metric(vy,pred),train_rows=len(y),iterations=model.n_iter_,
        transformed_features=len(x.columns),categorical_fields=cat,
        monthly={str(pd.Timestamp(m).date()):metric(vy[months==m],pred[months==m]) for m in np.unique(months)}))


def finalize(a):
    scores={}
    for arm in ['direct','scaled']:
        results=[json.loads((a.output/f'{fold}_{arm}.json').read_text())['validation'] for fold in ['early','recent']]
        scores[arm]=100*sum(r['absolute_error'] for r in results)/sum(r['actual_sum'] for r in results)
    base=[json.loads((a.source/f'{fold}_direct_full.json').read_text())['validation'] for fold in ['early','recent']]
    baseline=100*sum(r['absolute_error'] for r in base)/sum(r['actual_sum'] for r in base)
    challenger=min(scores,key=scores.get)
    save(a.output/'selection.json',dict(baseline_WAPE=baseline,candidate_WAPE=scores,challenger=challenger,
        selected=challenger if scores[challenger]<baseline else 'baseline'))
    # Read reused test only after candidate selection is persisted.
    with (a.output/f'recent_{challenger}.pkl').open('rb') as h:b=pickle.load(h)
    f=pd.read_parquet(a.source/'prepared.parquet',columns=list(dict.fromkeys(b['columns']+['year_month','item_code','forecast_month','target_usage'])),
        filters=[('forecast_month','>=',pd.Timestamp('2025-10-01')),('forecast_month','<=',pd.Timestamp('2025-12-01'))]).reset_index(drop=True)
    x,_=encode(f,b);pred=np.maximum(b['model'].predict(x)*scale(f,challenger),0)
    ref=pd.read_parquet(a.source/'test_direct_full_predictions.parquet')
    keys=['institution_code','department','item_code','forecast_month']
    result=f[keys+['target_usage']].assign(challenger=pred)
    for c in keys[:-1]:result[c]=result[c].astype(str);ref[c]=ref[c].astype(str)
    result=result.merge(ref[keys+['target_usage','prediction']],on=keys,how='outer',validate='one_to_one',indicator=True)
    if not result._merge.eq('both').all() or not np.allclose(result.target_usage_x,result.target_usage_y):raise ValueError('Test alignment failed')
    summary=dict(challenger=challenger,validation=scores,baseline_validation_WAPE=baseline,
        test_baseline=metric(result.target_usage_x,result.prediction),test_challenger=metric(result.target_usage_x,result.challenger),
        monthly={str(m.date()):{'baseline':metric(g.target_usage_x,g.prediction),'challenger':metric(g.target_usage_x,g.challenger)} for m,g in result.groupby('forecast_month')},
        serving_changed=False,independent_test=False)
    summary['improvement_pp']=summary['test_baseline']['WAPE']-summary['test_challenger']['WAPE']
    result.drop(columns='_merge').to_parquet(a.output/'test_predictions.parquet',index=False)
    save(a.output/'results.json',summary)


def suite(a):
    import psutil
    a.output.mkdir(parents=True,exist_ok=False)
    save(a.output/'plan.json',dict(arms=['direct','scaled'],model='HistGradientBoostingRegressor',max_iter=1200,
        source=str(a.source.resolve()),original_feature_count=74,
        categorical_policy='Top253 categories plus other; training-only frequency added; no target encoding',
        selection='Pooled May-June and August-September WAPE before reused Oct-Dec test',
        limitations=['Same original inputs, different categorical representation from LightGBM',
        'Same tree boosting family, not fundamentally different neural architecture','Reused evaluation periods, exploratory only'],
        source_manifest_sha256=hashlib.sha256((a.source/'manifest.json').read_bytes()).hexdigest()))
    tasks=[('fit',arm,fold) for arm in ['direct','scaled'] for fold in ['early','recent']]+[('finalize','direct','recent')]
    for action,arm,fold in tasks:
        label=f'{action}_{fold}_{arm}'
        save(a.output/'status.json',dict(stage='running',task=label))
        command=[sys.executable,'-u',str(Path(__file__).resolve()),action,'--arm',arm,'--fold',fold,'--source',str(a.source),'--output',str(a.output)]
        with (a.output/f'{label}.log').open('w',encoding='utf-8') as log:
            p=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
            started=time.monotonic()
            while p.poll() is None:
                reason='low_memory' if psutil.virtual_memory().available<1.5*1024**3 else ('task_timeout' if time.monotonic()-started>7200 else None)
                if reason:
                    owner=psutil.Process(p.pid)
                    owned=owner.children(recursive=True)+[owner]
                    for proc in owned:
                        try:proc.terminate()
                        except psutil.NoSuchProcess:pass
                    psutil.wait_procs(owned,timeout=10)
                    save(a.output/'status.json',dict(stage='paused',task=label,reason=reason));return
                time.sleep(5)
        if p.returncode:
            save(a.output/'status.json',dict(stage='failed',task=label,returncode=p.returncode));return
        time.sleep(15)
    save(a.output/'status.json',dict(stage='completed'))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['suite','fit','finalize'])
    p.add_argument('--arm',choices=['direct','scaled'],default='direct');p.add_argument('--fold',choices=['early','recent'],default='early')
    p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();globals()[a.action](a)
