"""Local-only, checkpointed model-family screening on a fixed retrospective panel.

No serving updates. Model selection uses August-September 2025 only.
October-December is a reused retrospective test, not a new holdout.
Run prepare, then suite. Every model uses the same ordered evaluation keys.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

import numpy as np
import pandas as pd
import psutil

ROOT = Path(__file__).resolve().parents[1]
MODELS = ['catboost', 'catboost_exact', 'croston_sba', 'tsb', 'nhits', 'patchtst', 'tft', 'chronos2', 'timesfm', 'prophet']
KEYS = ['forecast_origin_month', 'institution_code', 'department', 'item_code']
NUMERIC = ['month', 'quarter', 'history_months', 'series_observation_count', 'lag_1', 'lag_2',
           'lag_3', 'lag_6', 'lag_12', 'rolling_mean_3', 'rolling_std_3', 'rolling_mean_6',
           'rolling_std_6', 'rolling_mean_12', 'rolling_std_12', 'rolling_median_3',
           'expanding_mean', 'zero_rate_6', 'zero_rate_12', 'inbound_qty_lag_1',
           'month_end_stock_lag_1', 'stockout_rate_lag_1', 'disposal_qty_lag_1', 'is_winter', 'is_summer']
CATS = ['institution_code', 'department', 'standard_item_group_id', 'standard_item_family_id', 'standard_item_subtype_id']
BASELINES = ['stock_model_a_usage_only_pred', 'stock_model_a_usage_tweedie_pred',
             'temporal_ensemble_pred', 'baseline_rolling_mean_3_pred']


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def metric(y, p):
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    if len(y) != len(p) or not len(y) or not np.isfinite(y).all() or not np.isfinite(p).all():
        raise ValueError('Incomplete/nonfinite evaluation')
    if y.sum() <= 0:
        raise ValueError('Zero WAPE denominator')
    return {'N': len(y), 'WAPE': float(100*np.abs(y-p).sum()/y.sum()),
            'BIAS_PCT': float(100*(p-y).sum()/y.sum())}


def progress(out, model, stage, **details):
    state = dict(model=model, stage=stage, utc=pd.Timestamp.now(tz='UTC').isoformat(), **details)
    write_json(Path(out)/f'{model}_status.json', state)
    print(json.dumps(state, ensure_ascii=False), flush=True)


def contexts_from_frame(frame, width=24):
    """Frame must be ordered by segment/month. Gaps never become demand zeros."""
    ctx = np.full((len(frame), width), np.nan, dtype='float32')
    grouped = frame.groupby('series_segment_id', sort=False)['lag_1']
    for lag in range(width):
        ctx[:, width-1-lag] = grouped.shift(lag).to_numpy(dtype='float32')
    return ctx


def prepare(source, out):
    out.mkdir(parents=True, exist_ok=False)
    progress(out, 'prepare', 'reading')
    path = source/'outputs/stock_feature_table.parquet'
    columns = list(dict.fromkeys(NUMERIC+CATS+['year_month','forecast_month','item_code',
             'series_segment_id','historical_training_eligible','target_usage']))
    frame = pd.read_parquet(path, columns=columns)
    frame = frame.sort_values(['series_segment_id','year_month'], kind='stable').reset_index(drop=True)
    if frame.duplicated(['series_segment_id','year_month']).any():
        raise ValueError('Duplicate segment/month')
    context = contexts_from_frame(frame)
    eligible = frame.target_usage.ge(0) & frame.lag_1.ge(0)
    historical = frame.year_month.between('2018-01-01','2019-12-01') & frame.historical_training_eligible.fillna(False)
    train = eligible & (historical | frame.year_month.between('2024-01-01','2025-06-01'))
    evaluate = eligible & frame.forecast_month.between('2025-08-01','2025-12-01')
    meta = frame.loc[evaluate, ['year_month','forecast_month','institution_code','department','item_code','target_usage']].copy()
    meta.rename(columns={'year_month':'forecast_origin_month'}, inplace=True)
    for col in KEYS[1:]:
        meta[col] = meta[col].astype(str)
    meta['row_id'] = np.arange(len(meta))
    baseline = pd.read_csv(source/'outputs/stock_backtest_predictions.csv', usecols=KEYS+['actual_usage']+BASELINES,
                           dtype={c:str for c in KEYS[1:]}, parse_dates=[KEYS[0]])
    joined = meta.merge(baseline, on=KEYS, how='left', validate='one_to_one', sort=False).sort_values('row_id')
    if joined[BASELINES+['actual_usage']].isna().any().any() or not np.allclose(joined.actual_usage, joined.target_usage):
        raise ValueError('Baseline coverage/actual mismatch')
    joined.to_parquet(out/'evaluation.parquet', index=False)
    np.save(out/'contexts.npy', context[evaluate.to_numpy()])
    del context, baseline, meta, joined
    gc.collect()
    for name, mask in [('train',train), ('evaluation',evaluate)]:
        frame.loc[mask, NUMERIC+CATS+['target_usage']].to_parquet(out/f'{name}_tabular.parquet', index=False)
    # Native neural models observe the same available demand through July 2025.
    observed = historical | frame.year_month.between('2024-01-01','2025-07-01')
    panel = frame.loc[observed & frame.lag_1.ge(0), ['series_segment_id','year_month','lag_1']].rename(
        columns={'series_segment_id':'unique_id','year_month':'ds','lag_1':'y'})
    panel.to_parquet(out/'train_panel.parquet', index=False)
    progress(out, 'prepare', 'completed', train_rows=int(train.sum()), evaluation_rows=int(evaluate.sum()))
    write_json(out/'manifest.json', dict(source=str(source), source_bytes=path.stat().st_size,
        source_mtime_ns=path.stat().st_mtime_ns, numeric_features=NUMERIC, categorical_features=CATS,
        train_target_cutoff='2025-07', validation_months=['2025-08','2025-09'],
        test_months=['2025-10','2025-11','2025-12'], context_months=24,
        historical_weight=1.0, random_seed=42, models=MODELS,
        limitations=['Reused retrospective test, not new holdout',
          'Architectures have different native inputs/training algorithms',
          'One specified configuration per family: screening, not exhaustive optimization',
          'Unobserved months break series; never filled as zero demand',
          'No news/commodity supply-risk inputs in this demand-only experiment']))


def torch_setup():
    import torch
    torch.set_num_threads(2)
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA unavailable; no silent CPU substitution')
    torch.cuda.set_per_process_memory_fraction(0.50)
    return torch


def short_contexts(matrix):
    return [np.asarray(row[np.isfinite(row)], dtype='float32') for row in matrix]


def make_neural_frame(contexts, scale):
    lengths = np.array([len(x) for x in contexts])
    return pd.DataFrame({'unique_id':np.repeat(np.arange(len(contexts)), lengths),
        'ds':np.concatenate([pd.date_range(end='2025-07-01', periods=n, freq='MS').to_numpy() for n in lengths]),
        'y':np.concatenate(contexts)/scale})


def load_predictor(name, out, steps):
    if name in ['croston_sba','tsb']:
        from statsforecast.models import CrostonSBA, TSB
        model = CrostonSBA() if name == 'croston_sba' else TSB(alpha_d=0.1, alpha_p=0.1)
        def statistical(contexts):
            result = []
            for y in contexts:
                if not len(y):
                    raise ValueError('Empty context')
                result.append(float(model.forecast(y.astype(float), h=1)['mean'][0]))
            return np.array(result)
        return statistical
    if name == 'prophet':
        from prophet import Prophet
        from functools import lru_cache
        logging.getLogger('cmdstanpy').disabled = True
        @lru_cache(maxsize=100000)
        def one_prophet(context_bytes):
            y = np.frombuffer(context_bytes,dtype='float32')
            if len(y) < 2 or np.all(y == y[0]):
                return float(y[-1])  # Explicit constant/short-series policy.
            frame = pd.DataFrame({'ds':pd.date_range(end='2025-07-01',periods=len(y),freq='MS'),'y':y})
            model = Prophet(yearly_seasonality=False, weekly_seasonality=False, daily_seasonality=False,
                n_changepoints=min(5,len(y)//3), uncertainty_samples=0)
            model.fit(frame)
            return float(model.predict(pd.DataFrame({'ds':[pd.Timestamp('2025-08-01')]})).yhat.iloc[0])
        def prophet(contexts):
            return np.array([one_prophet(y.astype('float32').tobytes()) for y in contexts])
        return prophet
    torch = torch_setup()
    if name == 'chronos2':
        from chronos import Chronos2Pipeline
        pipeline = Chronos2Pipeline.from_pretrained('amazon/chronos-2', device_map='cuda',
            revision='29ec3766d36d6f73f0696f85560a422f50e8498c',local_files_only=True)
        def chronos(contexts):
            with torch.inference_mode():
                quantiles, _ = pipeline.predict_quantiles(contexts, prediction_length=1,
                    quantile_levels=[0.5], batch_size=64, cross_learning=False)
            return np.array([float(q.reshape(-1)[0]) for q in quantiles])
        return chronos
    if name == 'timesfm':
        import timesfm
        model = timesfm.TimesFM_2p5_200M_torch.from_pretrained('google/timesfm-2.5-200m-pytorch', torch_compile=False,
            revision='1d952420fba87f3c6dee4f240de0f1a0fbc790e3',local_files_only=True)
        model.compile(timesfm.ForecastConfig(max_context=32, max_horizon=1, normalize_inputs=True,
            per_core_batch_size=32, use_continuous_quantile_head=True, force_flip_invariance=False))
        def times(contexts):
            with torch.inference_mode():
                point, quantiles = model.forecast(horizon=1, inputs=contexts)
            return np.asarray(point)[:,0]
        return times
    from neuralforecast import NeuralForecast
    from neuralforecast.models import NHITS, PatchTST, TFT
    from neuralforecast.losses.pytorch import MAE
    from model_search_callbacks import SearchProgress
    model_dir = out/(name+'_model')
    scale_path = out/(name+'_scale.json')
    if model_dir.exists() and scale_path.exists():
        nf = NeuralForecast.load(str(model_dir))
        scale = json.loads(scale_path.read_text())['scale']
    else:
        train = pd.read_parquet(out/'train_panel.parquet')
        # A single global scale keeps absolute-error weights in original units.
        scale = max(float(train.y.mean()), 1)
        train['y'] = train.y/scale
        config = dict(h=1, input_size=12, max_steps=steps, loss=MAE(), scaler_type='identity',
            batch_size=128, windows_batch_size=512, inference_windows_batch_size=512,
            start_padding_enabled=True, random_seed=42, accelerator='gpu', devices=1,
            enable_progress_bar=False, logger=False, enable_checkpointing=False,
            enable_model_summary=False, dataloader_kwargs={'num_workers':0},
            callbacks=[SearchProgress(out,name)])
        if name == 'nhits':
            model = NHITS(**config, mlp_units=[[128,128]]*3, n_freq_downsample=[1,1,1])
        elif name == 'patchtst':
            model = PatchTST(**config, patch_len=4, stride=2, hidden_size=64, n_heads=4, encoder_layers=2)
        else:
            model = TFT(**config, hidden_size=64, n_head=4)
        nf = NeuralForecast(models=[model], freq='MS')
        nf.fit(df=train, val_size=0)
        nf.save(path=str(model_dir), overwrite=False, save_dataset=False)
        write_json(scale_path, {'scale':scale,'max_steps':steps,'loss':'MAE','scaler':'global_constant'})
        del train
        gc.collect()
    def neural(contexts):
        frame = make_neural_frame(contexts, scale)
        prediction = nf.predict(df=frame).sort_values('unique_id')
        col = next(c for c in prediction.columns if c not in ['unique_id','ds'])
        if len(prediction) != len(contexts):
            raise ValueError('Neural output alignment mismatch')
        return prediction[col].to_numpy()*scale
    return neural


def evaluate(name, out):
    meta = pd.read_parquet(out/'evaluation.parquet')
    p = np.load(out/f'{name}_predictions.npy', mmap_mode='r')
    if not np.isfinite(p).all():
        raise ValueError('Partial prediction cannot receive a full-panel score')
    valid = meta.forecast_month.lt('2025-10-01').to_numpy()
    result = dict(model=name, status='completed', validation=metric(meta.target_usage[valid],p[valid]),
                  test=metric(meta.target_usage[~valid],p[~valid]),
                  monthly={str(month.date()):metric(g.target_usage,p[g.index]) for month,g in meta.groupby('forecast_month')})
    if name=='prophet':
        contexts=np.load(out/'contexts.npy',mmap_mode='r')
        constant=(np.nanmax(contexts,axis=1)==np.nanmin(contexts,axis=1))
        result['constant_or_single_observation_policy_rows']=int(constant.sum())
        result['individually_fitted_rows']=int((~constant).sum())
    write_json(out/f'{name}_result.json', result)
    progress(out,name,'completed',**result['test'])
    aggregate(out)


def catboost(out, name='catboost'):
    from catboost import CatBoostRegressor
    train = pd.read_parquet(out/'train_tabular.parquet')
    ev = pd.read_parquet(out/'evaluation_tabular.parquet')
    meta = pd.read_parquet(out/'evaluation.parquet',columns=['forecast_month'])
    columns = NUMERIC+CATS
    for frame in [train,ev]:
        for col in CATS:
            frame[col] = frame[col].astype(str).fillna('missing')
        for col in NUMERIC:
            frame[col] = frame[col].astype('float32').replace([np.inf,-np.inf],np.nan)
    valid = meta.forecast_month.lt('2025-10-01').to_numpy()
    path = out/f'{name}.cbm'
    # Actual 1.2.10 GPU default is Gradient, despite current docs saying Exact.
    # Preserve the first run and explicitly compare exact MAE leaf estimation.
    extra = {'leaf_estimation_method':'Exact','boosting_type':'Plain'} if name=='catboost_exact' else {}
    model = CatBoostRegressor(iterations=1500, depth=6, learning_rate=.05, loss_function='MAE',
        eval_metric='MAE', task_type='GPU', devices='0', gpu_ram_part=.45, thread_count=2,
        border_count=64, max_ctr_complexity=1, random_seed=42, od_type='Iter', od_wait=100,
        train_dir=str(out/f'{name}_training'), verbose=100, **extra)
    if path.exists():
        model.load_model(path)
    else:
        model.fit(train[columns],train.target_usage,cat_features=CATS,
                  eval_set=(ev.loc[valid,columns],ev.target_usage[valid]))
        model.save_model(path)
    write_json(out/f'{name}_parameters.json',model.get_all_params())
    np.save(out/f'{name}_predictions.npy',np.maximum(model.predict(ev[columns]),0))
    evaluate(name,out)


def worker(name, out, steps):
    result_path = out/f'{name}_result.json'
    if result_path.exists():
        return
    progress(out,name,'starting')
    import importlib.metadata
    write_json(out/f'{name}_run_audit.json',dict(code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        neural_training_steps=steps,python=sys.version,
        packages={pkg:importlib.metadata.version(pkg) for pkg in
            ['torch','neuralforecast','catboost','chronos-forecasting','timesfm','statsforecast','prophet']},
        source_manifest_sha256=hashlib.sha256((out/'manifest.json').read_bytes()).hexdigest()))
    try:
        if name.startswith('catboost'):
            catboost(out,name)
            return
        context = np.load(out/'contexts.npy', mmap_mode='r')
        path = out/f'{name}_predictions.npy'
        if path.exists():
            predictions = np.lib.format.open_memmap(path, mode='r+')
        else:
            predictions = np.lib.format.open_memmap(path, mode='w+', dtype='float32',shape=(len(context),))
            predictions[:] = np.nan
            predictions.flush()
        predictor = load_predictor(name,out,steps)
        # Prophet intentionally retains small durable checkpoints: per-series fitting is expensive.
        batch = 128 if name == 'prophet' else 512
        started = time.time()
        for start in range(0,len(context),batch):
            stop = min(start+batch,len(context))
            if np.isfinite(predictions[start:stop]).all():
                continue
            values = short_contexts(context[start:stop])
            pred = np.asarray(predictor(values)).reshape(-1)
            if len(pred) != stop-start or not np.isfinite(pred).all():
                raise ValueError(f'Invalid predictions at {start}')
            predictions[start:stop] = np.maximum(pred,0)
            predictions.flush()
            if start % (batch*10) == 0 or stop==len(context):
                progress(out,name,'predicting',completed_rows=stop,total_rows=len(context),
                         elapsed_seconds=round(time.time()-started,1))
        evaluate(name,out)
    except Exception as exc:
        progress(out,name,'failed',error=repr(exc),traceback=traceback.format_exc())
        raise


def aggregate(out):
    meta = pd.read_parquet(out/'evaluation.parquet')
    valid = meta.forecast_month.lt('2025-10-01')
    rows = []
    for name in BASELINES:
        rows.append({'model':name,'status':'baseline',
            **{'validation_'+k:v for k,v in metric(meta.target_usage[valid],meta[name][valid]).items()},
            **{'test_'+k:v for k,v in metric(meta.target_usage[~valid],meta[name][~valid]).items()}})
    for name in MODELS:
        path = out/f'{name}_result.json'
        if path.exists():
            result = json.loads(path.read_text())
            rows.append({'model':name,'status':'completed',
                **{'validation_'+k:v for k,v in result['validation'].items()},
                **{'test_'+k:v for k,v in result['test'].items()}})
        else:
            status_path = out/f'{name}_status.json'
            state = json.loads(status_path.read_text()) if status_path.exists() else {'stage':'not_started'}
            rows.append({'model':name,'status':state['stage']})
    pd.DataFrame(rows).to_csv(out/'comparison.csv',index=False,encoding='utf-8-sig')
    scored=[row for row in rows if 'validation_WAPE' in row]
    best=min(scored,key=lambda row:row['validation_WAPE'])
    incomplete=[row['model'] for row in rows if row['status'] not in ['baseline','completed']]
    write_json(out/'selection.json',dict(provisional_validation_winner=best['model'],
        validation_WAPE=best['validation_WAPE'],test_WAPE=best['test_WAPE'],
        incomplete_models=incomplete,all_models_completed=not incomplete,serving_changed=False,
        limitation='Retrospective reused test; selection uses validation only'))
    lines=['# 예측모델 비교 진행 현황', '',
        '갱신 시각(UTC): '+pd.Timestamp.now(tz='UTC').isoformat(), '',
        '모델 선택 기준은 검증 WAPE입니다. 평가 구간은 이미 확인했던 과거 구간이며, 서비스 모델은 변경하지 않습니다.', '',
        f"현재 검증 WAPE 최저: {best['model']} ({best['validation_WAPE']:.4f}%).", '',
        '미완료 모델: '+(', '.join(incomplete) if incomplete else '없음'), '']
    for row in rows:
        if 'validation_WAPE' in row:
            lines.append(f"- {row['model']}: 검증 WAPE {row['validation_WAPE']:.4f}%, 평가 WAPE {row['test_WAPE']:.4f}%, 평가 BIAS {row['test_BIAS_PCT']:+.4f}%.")
        else:
            lines.append(f"- {row['model']}: {row['status']} (전체 평가 점수 없음).")
    (out/'진행현황.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def stop_owned_tree(child):
    """Windows venv launchers have a real Python child: stop that tree too."""
    try:
        parent = psutil.Process(child.pid)
        owned = parent.children(recursive=True) + [parent]
    except psutil.NoSuchProcess:
        return
    for process in reversed(owned):
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(owned, timeout=10)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=5)
    child.wait(timeout=5)


def suite(out,steps,models,timeout):
    lock = out/'suite.lock'
    # Atomic exclusive lock; stale locks require explicit inspection, never implicit restart.
    with lock.open('x') as handle:
        handle.write(str(os.getpid()))
    interrupted = False
    child = None
    try:
        for name in models:
            if (out/f'{name}_result.json').exists():
                continue
            progress(out,'suite','running',current_model=name)
            with (out/f'{name}.log').open('a',encoding='utf-8') as log:
                child = subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'worker',
                    '--output',str(out),'--model',name,'--steps',str(steps)],stdout=log,stderr=subprocess.STDOUT)
                started = time.time()
                reason = None
                while child.poll() is None:
                    if time.time()-started > timeout:
                        reason = 'time_budget_exceeded_checkpoint_preserved'
                    if psutil.virtual_memory().available < 1.5*1024**3:
                        reason = 'low_system_memory'
                    try:
                        temp = subprocess.check_output(['nvidia-smi','--query-gpu=temperature.gpu','--format=csv,noheader,nounits'],timeout=5,text=True)
                        if int(temp.strip().splitlines()[0]) >= 82:
                            reason = 'gpu_temperature_limit'
                    except (OSError,ValueError,subprocess.SubprocessError):
                        pass
                    if reason:
                        stop_owned_tree(child)
                        progress(out,name,'paused',reason=reason)
                        if reason != 'time_budget_exceeded_checkpoint_preserved':
                            interrupted = True
                        break
                    time.sleep(5)
                if child.returncode and not reason:
                    if not (out/f'{name}_status.json').exists() or json.loads((out/f'{name}_status.json').read_text())['stage'] != 'failed':
                        progress(out,name,'failed',returncode=child.returncode)
            aggregate(out)
            if interrupted:
                break
            time.sleep(15)
        complete = all((out/f'{name}_result.json').exists() for name in models)
        progress(out,'suite','completed' if complete else 'needs_review',models=models)
    finally:
        if child is not None and child.poll() is None:
            stop_owned_tree(child)
        lock.unlink()  # Only this process's transient lock, never artifacts/checkpoints.


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action',choices=['prepare','worker','suite','aggregate'])
    p.add_argument('--source',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--model',choices=MODELS)
    p.add_argument('--models',nargs='+',choices=MODELS,default=MODELS)
    p.add_argument('--steps',type=int,default=1500)
    p.add_argument('--timeout',type=int,default=3600)
    args = p.parse_args()
    if args.action=='prepare':
        prepare(args.source.resolve(),args.output.resolve())
    elif args.action=='worker':
        worker(args.model,args.output.resolve(),args.steps)
    elif args.action=='aggregate':
        aggregate(args.output.resolve())
    else:
        suite(args.output.resolve(),args.steps,args.models,args.timeout)


if __name__ == '__main__':
    main()
