"""Predeclared first-stage specialist training, using a hash-pinned TabM trainer."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
TRAINER=ROOT.parent/'tabm-search-20260915/scripts/train_tabm.py'
EXPECTED='42134fae9e052e59bb355c0d1cbd71a64bc62bad14aa7e69a2f96621978bb8b7'
if hashlib.sha256(TRAINER.read_bytes()).hexdigest()!=EXPECTED:
    raise RuntimeError('Dependency changed; review before running')
sys.path.insert(0,str(TRAINER.parent))
import train_tabm as base
import numpy as np
import pandas as pd
import psutil

GROUPS=('short_history','moderate')


def states(f):
    mean=f.rolling_mean_3.to_numpy(float)
    cv=np.divide(f.rolling_std_3.to_numpy(float),mean,out=np.full(len(f),np.inf),where=mean>0)
    return np.select([f.history_months.to_numpy()<6,f.lag_1.to_numpy()==0,cv<=.2,cv>=.8],
                     ['short_history','last_zero','stable','volatile'],default='moderate')


def specialist_masks(f,fold,group):
    tr,va=base.masks(f,fold)
    eligible=states(f)==group
    return tr&eligible,va&eligible


def fit(a):
    original=base.masks
    # Scoped adapter restores the dependency even when fitting fails.
    def select(f,fold):
        tr,va=original(f,fold)
        eligible=states(f)==a.group
        return tr&eligible,va&eligible
    base.masks=select
    try:
        base.fit(SimpleNamespace(source=a.source,output=a.output/a.group,fold='early'))
    finally:
        base.masks=original


def evaluate(a):
    f=pd.read_parquet(a.source/'prepared.parquet',columns=['forecast_month','target_usage',
          'history_months','lag_1','rolling_mean_3','rolling_std_3'],
          filters=[('forecast_month','>=',pd.Timestamp('2025-05-01')),
                   ('forecast_month','<=',pd.Timestamp('2025-06-01'))])
    b=pd.read_parquet(ROOT.parent/'ingredient-wape-20260914/outputs/ingredient_wape_v1/early_paired.parquet')
    assert np.array_equal(f.target_usage,b.actual) and np.array_equal(f.forecast_month,b.month)
    labels=states(f);y=f.target_usage.to_numpy(float); baseline=b.baseline.to_numpy(float)
    candidate=baseline.copy();groups={}
    # Fixed two-group replacement; do not cherry-pick winning groups after evaluation.
    for group in GROUPS:
        rows=labels==group
        p=pd.read_parquet(a.output/group/'early_predictions.parquet')
        assert np.array_equal(y[rows],p.actual) and np.array_equal(f.forecast_month.to_numpy()[rows],p.month)
        candidate[rows]=p.prediction.to_numpy()
        groups[group]=dict(baseline=base.metric(y[rows],baseline[rows]),specialist=base.metric(y[rows],candidate[rows]))
    before,after=base.metric(y,baseline),base.metric(y,candidate)
    gain=before['WAPE']-after['WAPE']
    base.save(a.output/'screening.json',dict(baseline=before,specialist=after,improvement_pp=gain,groups=groups,
        next_step='eligible_for_second_validation' if gain>=.3 else 'stop_this_setting',
        rule='Both fixed groups, full-population gain at least 0.3pp; screening is not independent confirmation',
        second_validation_started=False,reused_test_read=False))


def run(a):
    a.output.mkdir(parents=True,exist_ok=False)
    try:
        digest=hashlib.sha256((a.source/'prepared.parquet').read_bytes()).hexdigest()
        assert digest=='fe36707a5caf608161b8e0f6f5d503fd9f74b8a02463313a70b54cc77e30890c'
        base.save(a.output/'manifest.json',dict(trainer_sha256=EXPECTED,source_sha256=digest,
             boot_time=psutil.boot_time(),groups=GROUPS,fold='early',minimum_full_WAPE_gain_pp=.3))
        for group in GROUPS:
            if psutil.virtual_memory().available<1.5*2**30: raise RuntimeError('Low memory before start')
            (a.output/group).mkdir()
            with (a.output/f'{group}.log').open('x') as log:
                p=subprocess.Popen([sys.executable,'-u',__file__,'--mode','fit','--group',group,
                    '--source',str(a.source),'--output',str(a.output)],stdout=log,stderr=subprocess.STDOUT)
                base.event(a.output,stage='training_child_started',group=group,pid=p.pid)
                start=time.monotonic()
                while p.poll() is None:
                    if psutil.virtual_memory().available<1.5*2**30 or time.monotonic()-start>7200:
                        owned=psutil.Process(p.pid).children(recursive=True)
                        for child in reversed(owned):
                            try:child.terminate()
                            except psutil.NoSuchProcess:pass
                        p.terminate();p.wait(timeout=30);psutil.wait_procs(owned,timeout=30)
                        raise RuntimeError('Resource safety stop; checkpoints preserved')
                    time.sleep(5)
                if p.returncode: raise RuntimeError(f'{group}: exit {p.returncode}; no automatic retry')
        evaluate(a)
        base.event(a.output,stage='screening_completed')
    except Exception as exc:
        base.event(a.output,stage='failed',error=str(exc));raise


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=['run','fit'],default='run')
    p.add_argument('--group',choices=GROUPS,default='short_history')
    p.add_argument('--source',type=Path,default=ROOT.parent/'residual-demand-20260914/outputs/residual_demand_v1')
    p.add_argument('--output',type=Path,default=ROOT/'outputs/state_specialist_v1')
    a=p.parse_args();{'run':run,'fit':fit}[a.mode](a)
