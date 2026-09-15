"""Full eligible-data TabM challenge; CPU-only, temporal selection, immutable epochs."""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

for name, value in {'OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'1',
                    'MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1'}.items():
    os.environ[name] = value
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'outputs/tabm_runtime'))
import numpy as np
import pandas as pd
import psutil
import torch
from tabm import TabM
torch.set_num_threads(2)


def save(path, obj):
    # Final artifacts are exclusive; changing progress belongs in append-only events.
    with path.open('x', encoding='utf-8') as h:
        json.dump(obj, h, ensure_ascii=False, indent=2, allow_nan=False)


def event(out, **kw):
    row = dict(time=time.time(), **kw)
    with (out/'events.jsonl').open('a', encoding='utf-8') as h:
        h.write(json.dumps(row, allow_nan=False)+'\n')
    print(json.dumps(row), flush=True)


def metric(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    assert y.shape == p.shape and np.isfinite(y).all() and np.isfinite(p).all()
    assert y.sum() > 0 and (y >= 0).all()
    return dict(N=len(y), actual_sum=float(y.sum()), absolute_error=float(abs(y-p).sum()),
                WAPE=float(100*abs(y-p).sum()/y.sum()), BIAS=float(100*(p-y).sum()/y.sum()))


def fit_encoder(f, cols):
    b = dict(columns=cols, categories={}, numeric={})
    for c in cols:
        if isinstance(f[c].dtype, pd.CategoricalDtype) or not pd.api.types.is_numeric_dtype(f[c]):
            b['categories'][c] = f[c].astype('string').dropna().unique().tolist()
        else:
            z = pd.to_numeric(f[c], errors='coerce').to_numpy(dtype=float, na_value=np.nan)
            z = z[np.isfinite(z)]
            z = np.sign(z)*np.log1p(abs(z))
            b['numeric'][c] = [float(z.mean()) if len(z) else 0.,
                               max(float(z.std()), 1e-6) if len(z) else 1.]
    return b


def encode(f, b):
    x = np.empty((len(f), 2*len(b['numeric'])), dtype='float32')
    for i, (c, (mean, std)) in enumerate(b['numeric'].items()):
        z = pd.to_numeric(f[c], errors='coerce').to_numpy(dtype=float, na_value=np.nan)
        ok = np.isfinite(z)
        z = np.where(ok, z, 0.)
        z = (np.sign(z)*np.log1p(abs(z))-mean)/std
        x[:,2*i] = np.where(ok,z,0).astype('float32')
        x[:,2*i+1] = ~ok
    cat = np.empty((len(f), len(b['categories'])), dtype='int64')
    for i, (c, values) in enumerate(b['categories'].items()):
        cat[:,i] = pd.Categorical(f[c].astype('string'), categories=values).codes.astype('int64')+1
    assert np.isfinite(x).all()
    return torch.from_numpy(x), torch.from_numpy(cat)


def scale(f):
    s = f.rolling_mean_3.to_numpy(dtype=float, na_value=np.nan)
    return np.maximum(np.where(np.isfinite(s), s, 0), 1).astype('float32')


class Network(torch.nn.Module):
    def __init__(self, b):
        super().__init__()
        self.embeddings = torch.nn.ModuleList([
            torch.nn.Embedding(len(v)+1, 8, padding_idx=0) for v in b['categories'].values()])
        self.core = TabM.make(n_num_features=2*len(b['numeric'])+8*len(self.embeddings),
                             d_out=1, k=4, n_blocks=2, d_block=128, dropout=.1)

    def forward(self, x, c):
        z = torch.cat([x]+[e(c[:,i]) for i,e in enumerate(self.embeddings)], dim=1)
        return self.core(z).squeeze(-1)


def member_loss(p, y, s):
    # Exactly original-unit MAE per member, before a fixed train-only gradient scale.
    return ((p-y[:,None]/s[:,None]).abs()*s[:,None]).mean()


@torch.no_grad()
def predict(model, x, c, s):
    model.eval()
    result = np.empty(len(x), dtype=float)
    for start in range(0,len(x),2048):
        end = start+2048
        result[start:end] = model(x[start:end], c[start:end]).mean(1).numpy()*s[start:end]
    return np.maximum(result,0)


def masks(f, fold):
    cutoff, start, end = fold
    history = f.year_month.between('2018-01-01','2019-12-01') & f.historical_training_eligible.fillna(False)
    return (f.forecast_month.le(cutoff) & (history | f.year_month.ge('2024-01-01')),
            f.forecast_month.between(start,end))


def fit(a):
    torch.manual_seed(42)
    meta = json.loads((a.source/'manifest.json').read_text(encoding='utf-8'))
    cols = meta['features']
    assert len(cols) == 74
    fold = meta['folds'][a.fold]
    event(a.output, stage='loading', fold=a.fold)
    f = pd.read_parquet(a.source/'prepared.parquet', columns=cols+[
        'year_month','forecast_month','target_usage','historical_training_eligible'],
        filters=[('forecast_month','<=',pd.Timestamp(fold[2]))])
    tr, va = masks(f, fold)
    train, valid = f.loc[tr,cols], f.loc[va,cols]
    y = torch.tensor(f.loc[tr,'target_usage'].to_numpy(float), dtype=torch.float32)
    vy = f.loc[va,'target_usage'].to_numpy(float)
    months = f.loc[va,'forecast_month'].to_numpy()
    s, vs = torch.from_numpy(scale(train)), scale(valid)
    assert torch.isfinite(y).all() and (y>=0).all()
    b = fit_encoder(train,cols)
    x,c = encode(train,b)
    vx,vc = encode(valid,b)
    del f,train,valid
    gc.collect()
    save(a.output/f'{a.fold}_encoder.json',b)
    model = Network(b)
    opt = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.0001)
    divisor = max(float(y.mean()),1.)
    best, best_epoch, stale = float('inf'), 0, 0
    event(a.output, stage='training_started', fold=a.fold, train_rows=len(y), validation_rows=len(vy),
          available_GiB=psutil.virtual_memory().available/2**30)
    for epoch in range(1,31):
        started = time.monotonic()
        model.train()
        order = torch.randperm(len(y))
        for step, start in enumerate(range(0,len(y),2048)):
            idx = order[start:start+2048]
            opt.zero_grad(set_to_none=True)
            loss = member_loss(model(x[idx],c[idx]),y[idx],s[idx])/divisor
            if not torch.isfinite(loss):
                raise ValueError('Nonfinite loss')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(),10.,error_if_nonfinite=True)
            opt.step()
            if step % 250 == 0:
                event(a.output, stage='batch',fold=a.fold,epoch=epoch,batch=step,loss=float(loss.detach()))
        p = predict(model,vx,vc,vs)
        score = metric(vy,p)
        # Preserve every epoch, including optimizer and RNG for audited manual resumption.
        torch.save(dict(model=model.state_dict(),optimizer=opt.state_dict(),epoch=epoch,
                        rng=torch.get_rng_state()), a.output/f'{a.fold}_epoch_{epoch:02d}.pt')
        event(a.output, stage='epoch_completed', fold=a.fold,epoch=epoch,
              seconds=time.monotonic()-started, **score)
        if score['WAPE'] < best:
            best, best_epoch, stale = score['WAPE'],epoch,0
        else:
            stale += 1
        if stale >= 5:
            break
    model.load_state_dict(torch.load(a.output/f'{a.fold}_epoch_{best_epoch:02d}.pt',weights_only=True)['model'])
    p = predict(model,vx,vc,vs)
    pd.DataFrame(dict(month=months,actual=vy,prediction=p)).to_parquet(a.output/f'{a.fold}_predictions.parquet',index=False)
    save(a.output/f'{a.fold}_result.json', dict(validation=metric(vy,p),best_epoch=best_epoch,
         train_rows=len(y),monthly={str(pd.Timestamp(m).date()):metric(vy[months==m],p[months==m]) for m in np.unique(months)}))


def finalize(a):
    results = [json.loads((a.output/f'{fold}_result.json').read_text()) for fold in ['early','recent']]
    base = [json.loads((a.source/f'{fold}_direct_full.json').read_text())['validation'] for fold in ['early','recent']]
    def pooled(rows):
        return 100*sum(r['absolute_error'] for r in rows)/sum(r['actual_sum'] for r in rows)
    candidate, baseline = pooled([r['validation'] for r in results]),pooled(base)
    save(a.output/'selection.json', dict(baseline_WAPE=baseline,TabM_WAPE=candidate,
         improvement_pp=baseline-candidate,selected='TabM' if candidate<baseline else 'baseline'))
    # Reused test is accessed only after validation selection; never used for tuning.
    b = json.loads((a.output/'recent_encoder.json').read_text(encoding='utf-8'))
    f = pd.read_parquet(a.source/'prepared.parquet',filters=[('forecast_month','>=',pd.Timestamp('2025-10-01')),
                                                            ('forecast_month','<=',pd.Timestamp('2025-12-01'))])
    keys = ['institution_code','department','item_code','forecast_month']
    baseline_pred = pd.read_parquet(a.source/'test_direct_full_predictions.parquet')
    f = f.merge(baseline_pred[keys+['target_usage','prediction']],on=keys,how='outer',
                validate='one_to_one',indicator=True,suffixes=('','_base'))
    assert f['_merge'].eq('both').all() and np.array_equal(f.target_usage,f.target_usage_base)
    x,c = encode(f,b)
    model = Network(b)
    model.load_state_dict(torch.load(a.output/f'recent_epoch_{results[1]["best_epoch"]:02d}.pt',weights_only=True)['model'])
    p = predict(model,x,c,scale(f))
    y = f.target_usage.to_numpy(float)
    bp = f.prediction.to_numpy(float)
    out = f[keys+['target_usage','prediction']].rename(columns={'prediction':'baseline'})
    out['TabM'] = p
    out.to_parquet(a.output/'reused_test_predictions.parquet', index=False)
    save(a.output/'results.json',dict(validation=results,reused_test=dict(baseline=metric(y,bp),TabM=metric(y,p)),
         improvement_pp=metric(y,bp)['WAPE']-metric(y,p)['WAPE'],
         monthly={str(m.date()):dict(baseline=metric(y[f.forecast_month.eq(m)],bp[f.forecast_month.eq(m)]),
                     TabM=metric(y[f.forecast_month.eq(m)],p[f.forecast_month.eq(m)])) for m in sorted(f.forecast_month.unique())},
         limitation='Reused evaluation months, exploratory only; no automatic service replacement'))


def supervise(a):
    a.output.mkdir(parents=True,exist_ok=False)
    try:
        digest = hashlib.sha256((a.source/'prepared.parquet').read_bytes()).hexdigest()
        assert digest == 'fe36707a5caf608161b8e0f6f5d503fd9f74b8a02463313a70b54cc77e30890c'
        save(a.output/'run.json',dict(source_sha256=digest,script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             boot_time=psutil.boot_time(),batch=2048,epochs=30,patience=5,seed=42,torch=torch.__version__,threads=2))
        for mode,fold in [('fit','early'),('fit','recent'),('finalize','recent')]:
            if psutil.virtual_memory().available < 1.5*2**30:
                raise RuntimeError('Insufficient memory before child start')
            cmd = [sys.executable,'-u',__file__,'--mode',mode,'--fold',fold,'--source',str(a.source),'--output',str(a.output)]
            with (a.output/f'{mode}_{fold}.log').open('x') as log:
                child = subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
                event(a.output,stage='child_started',mode=mode,fold=fold,pid=child.pid)
                begin = time.monotonic()
                while child.poll() is None:
                    if psutil.virtual_memory().available < 1.5*2**30 or time.monotonic()-begin > 7200:
                        # Windows venv may spawn a second interpreter. Stop this owned
                        # subtree, not just its launcher, and never unrelated Python.
                        parent = psutil.Process(child.pid)
                        descendants = parent.children(recursive=True)
                        for process in reversed(descendants):
                            try:
                                process.terminate()
                            except psutil.NoSuchProcess:
                                pass
                        child.terminate()
                        child.wait(timeout=30)
                        psutil.wait_procs(descendants,timeout=30)
                        raise RuntimeError('Safety stop: memory floor or two-hour task limit')
                    time.sleep(5)
                if child.returncode:
                    raise RuntimeError(f'{mode}/{fold} failed: exit {child.returncode}')
        event(a.output,stage='completed')
    except Exception as exc:
        event(a.output,stage='failed',error=str(exc))
        raise


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=ROOT.parent/'residual-demand-20260914/outputs/residual_demand_v1')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/tabm_v1')
    parser.add_argument('--mode',choices=['run','fit','finalize'],default='run')
    parser.add_argument('--fold',choices=['early','recent'],default='early')
    a=parser.parse_args()
    {'run':supervise,'fit':fit,'finalize':finalize}[a.mode](a)
