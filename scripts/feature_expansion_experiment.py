"""Sequential full-data feature ablation. Outputs stay in an isolated directory."""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

import numpy as np
import pandas as pd
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.modeling.feature_expansion import BEHAVIOR, CALENDAR, PEER, expand

BASE = ['institution_code', 'department', 'normal_outbound_signed_sum',
        'model_demand_positive_sum', 'negative_normal_outbound_count',
        'negative_normal_outbound_amount', 'data_period', 'standard_item_definition_key',
        'standard_item_group_id', 'standard_item_family_id', 'standard_item_subtype_id',
        'standard_item_specification', 'standard_item_unit_code', 'standardization_match_method',
        'year', 'month', 'quarter', 'history_months', 'series_observation_count',
        'lag_1', 'lag_2', 'lag_3', 'lag_6', 'lag_12',
        *[f'{c}_lag_{i}' for c in ['inbound_qty', 'month_end_stock', 'stockout_rate', 'disposal_qty'] for i in [1,2,3]],
        'auto_disposal_adjustment_qty_lag_1', 'rolling_mean_3', 'rolling_std_3',
        'rolling_mean_6', 'rolling_std_6', 'rolling_mean_12', 'rolling_std_12',
        'rolling_median_3', 'expanding_mean', 'zero_rate_6', 'zero_rate_12',
        'is_winter', 'is_summer', 'same_month_last_year', 'yoy_growth_rate']
ARMS = {'baseline51': [], 'behavior': BEHAVIOR, 'calendar': CALENDAR, 'peer': PEER,
        'behavior_calendar': BEHAVIOR + CALENDAR, 'all': BEHAVIOR + CALENDAR + PEER}
FOLDS = {'early': ('2025-04-01', '2025-05-01', '2025-06-01'),
         'recent': ('2025-07-01', '2025-08-01', '2025-09-01')}
KEYS = ['forecast_origin_month', 'institution_code', 'department', 'item_code']
# Snapshot of the local usage-only model, not defaults or GPU-screening settings.
PARAMS = dict(objective='regression_l1', n_estimators=1999,
    learning_rate=0.02723494881078291, num_leaves=249, min_child_samples=126,
    colsample_bytree=0.6133567436538621, subsample=0.733063041720666,
    subsample_freq=1, reg_alpha=0.02041030416355554, reg_lambda=0.07198334621828253,
    random_state=42, n_jobs=2, force_col_wise=True, histogram_pool_size=64, verbosity=-1)


def write(path, obj):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def status(out, **kw):
    state = dict(utc=pd.Timestamp.now(tz='UTC').isoformat(), **kw)
    write(out/'status.json', state)
    print(json.dumps(state, ensure_ascii=False), flush=True)


def metric(y, p):
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    if y.shape != p.shape or not len(y) or not np.isfinite(y).all() or not np.isfinite(p).all() or y.sum() <= 0:
        raise ValueError('Invalid/incomplete metric input')
    return dict(N=len(y), absolute_error=float(np.abs(p-y).sum()), actual_sum=float(y.sum()),
                WAPE=float(100*np.abs(p-y).sum()/y.sum()), BIAS=float(100*(p-y).sum()/y.sum()))


def prepare(args):
    out = args.output
    cols = list(dict.fromkeys(BASE + ['year_month', 'forecast_month', 'series_segment_id',
                            'item_code', 'target_usage', 'historical_training_eligible']))
    source = args.source/'outputs/stock_feature_table.parquet'
    f = pd.read_parquet(source, columns=cols)
    for c in f.select_dtypes('float64'):
        f[c] = f[c].astype('float32')
    f = expand(f)
    eligible = f.target_usage.ge(0) & f.lag_1.ge(0)
    f = f.loc[eligible].reset_index(drop=True)
    meta = f[f.forecast_month.between('2025-08-01','2025-12-01')][
        ['year_month','institution_code','department','item_code','target_usage']].rename(columns={'year_month':'forecast_origin_month'})
    ref = pd.read_parquet(args.reference)
    for c in KEYS[1:]:
        meta[c] = meta[c].astype(str)
        ref[c] = ref[c].astype(str)
    joined = meta.merge(ref[KEYS+['target_usage']], on=KEYS, how='outer', validate='one_to_one', indicator=True)
    if not joined['_merge'].eq('both').all() or not np.allclose(joined.target_usage_x,joined.target_usage_y):
        raise ValueError('Evaluation population differs from previous experiment')
    audit = {}
    for split, mask in [('train',f.forecast_month.le('2025-07-01')), ('validation',f.forecast_month.between('2025-08-01','2025-09-01'))]:
        # Training quality report excludes unapproved history and the 2020-23 gap.
        if split == 'train':
            mask &= ((f.year_month.between('2018-01-01','2019-12-01') & f.historical_training_eligible.fillna(False)) | f.year_month.ge('2024-01-01'))
        audit[split] = {c: dict(missing_rate=float(f.loc[mask,c].isna().mean()),
                                   distinct=int(f.loc[mask,c].nunique())) for c in BASE+BEHAVIOR+CALENDAR+PEER}
    write(out/'feature_quality.json', audit)
    f.to_parquet(out/'prepared.parquet', index=False)
    write(out/'manifest.json', dict(source=str(source), source_bytes=source.stat().st_size,
        source_mtime_ns=source.stat().st_mtime_ns, reference=str(args.reference),
        rows=len(f), evaluation_rows=len(meta), features={k:BASE+v for k,v in ARMS.items()},
        params=PARAMS, folds=FOLDS, source_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        limitations=['Existing model feature/parameter snapshot; refit, not exact saved-model reproduction',
        'Early stopping uses validation only; equal 1999-round cap per arm',
        'Two historical validation folds; Oct-Dec test already reused',
        'Calendar weekdays exclude neither holidays nor actual clinic closures',
        'Peers require all institutions origin-month records available together',
        'No conversion across units; negative/unknown observations are not zero demand']))


def fit(args):
    import lightgbm as lgb
    import pickle
    out, arm, fold = args.output, args.arm, args.fold
    cols = BASE + ARMS[arm]
    f = pd.read_parquet(out/'prepared.parquet', columns=cols+['year_month','forecast_month','target_usage','historical_training_eligible'])
    cutoff, start, end = FOLDS[fold]
    historical = f.year_month.between('2018-01-01','2019-12-01') & f.historical_training_eligible.fillna(False)
    train = f.forecast_month.le(cutoff) & (historical | f.year_month.ge('2024-01-01'))
    valid = f.forecast_month.between(start,end)
    x, v = f.loc[train,cols].copy(), f.loc[valid,cols].copy()
    y, vy = f.loc[train,'target_usage'].copy(), f.loc[valid,'target_usage'].copy()
    months = f.loc[valid,'forecast_month'].copy()
    del f
    gc.collect()
    categories, medians = {}, {}
    for c in cols:
        if isinstance(x[c].dtype, pd.CategoricalDtype) or not pd.api.types.is_numeric_dtype(x[c]):
            values = x[c].astype('string').fillna('__MISSING__')
            categories[c] = values.unique().tolist()
            x[c] = pd.Categorical(values, categories=categories[c])
            v[c] = pd.Categorical(v[c].astype('string').fillna('__MISSING__'), categories=categories[c])
        else:
            x[c] = x[c].replace([np.inf,-np.inf],np.nan)
            medians[c] = float(x[c].median()) if x[c].notna().any() else 0.0
            x[c] = x[c].fillna(medians[c]).astype('float32')
            v[c] = v[c].replace([np.inf,-np.inf],np.nan).fillna(medians[c]).astype('float32')
    model = lgb.LGBMRegressor(**PARAMS)
    model.fit(x,y,eval_set=[(v,vy)],eval_metric='l1',
              callbacks=[lgb.early_stopping(100,verbose=False), lgb.log_evaluation(100)])
    pred = np.maximum(model.predict(v),0)
    stem = f'{fold}_{arm}'
    # Model first, result marker last: a result always has a reusable model.
    with (out/f'{stem}.pkl').open('wb') as handle:
        pickle.dump(dict(model=model,columns=cols,categories=categories,medians=medians),handle)
    write(out/f'{stem}.json', dict(arm=arm,fold=fold,train_rows=len(y),validation=metric(vy,pred),
        best_iteration=model.best_iteration_, monthly={str(m.date()):metric(vy[months.eq(m)],pred[months.eq(m)]) for m in months.unique()}))


def finalize(args):
    import pickle
    out = args.output
    scores = {}
    for arm in ARMS:
        results = [json.loads((out/f'{fold}_{arm}.json').read_text())['validation'] for fold in FOLDS]
        scores[arm] = 100*sum(r['absolute_error'] for r in results)/sum(r['actual_sum'] for r in results)
    winner = min(scores,key=scores.get)
    # Lock selection before reading any test outcomes.
    write(out/'selection.json',dict(winner=winner,pooled_validation_WAPE=scores,serving_changed=False))
    reference = pd.read_parquet(args.reference)
    reference = reference[reference.forecast_month.ge('2025-10-01')].copy()
    for c in KEYS[1:]:
        reference[c] = reference[c].astype(str)
    evaluations = {}
    for arm in dict.fromkeys(['baseline51',winner]):
        with (out/f'recent_{arm}.pkl').open('rb') as handle:
            b = pickle.load(handle)
        cols = b['columns']
        f = pd.read_parquet(out/'prepared.parquet',columns=list(dict.fromkeys(cols+['year_month','item_code','forecast_month','target_usage'])),
                            filters=[('forecast_month','>=',pd.Timestamp('2025-10-01')),('forecast_month','<=',pd.Timestamp('2025-12-01'))])
        x = f[cols].copy()
        for c,cats in b['categories'].items():
            x[c] = pd.Categorical(x[c].astype('string').fillna('__MISSING__'),categories=cats)
        for c,median in b['medians'].items():
            x[c] = x[c].replace([np.inf,-np.inf],np.nan).fillna(median).astype('float32')
        f['prediction'] = np.maximum(b['model'].predict(x),0)
        f = f.rename(columns={'year_month':'forecast_origin_month'})
        for c in KEYS[1:]:
            f[c] = f[c].astype(str)
        aligned = reference.merge(f[KEYS+['prediction']],on=KEYS,how='left',validate='one_to_one')
        evaluations[arm] = dict(overall=metric(aligned.target_usage,aligned.prediction),
            monthly={str(m.date()):metric(g.target_usage,g.prediction) for m,g in aligned.groupby('forecast_month')})
        aligned[KEYS+['forecast_month','target_usage','prediction']].to_parquet(out/f'test_{arm}_predictions.parquet',index=False)
    for name in ['stock_model_a_usage_only_pred','temporal_ensemble_pred']:
        evaluations[name] = dict(overall=metric(reference.target_usage,reference[name]),
            monthly={str(m.date()):metric(g.target_usage,g[name]) for m,g in reference.groupby('forecast_month')})
    write(out/'test_results.json',evaluations)
    lines = ['# 입력 변수 확장 실험', '',
             '기존 저장 모델과의 완전 재현이 아니라, 같은 설정으로 재학습한 51개 변수 모델을 기준으로 한 비교입니다.',
             '후보는 두 과거 검증 구간의 절대오차 합/실제량 합으로 선택했습니다. 2025년 10~12월은 이미 사용한 평가 구간입니다.',
             '', f'검증 선택 후보: {winner}', '', '## 검증 WAPE', '']
    lines += [f'- {a}: {s:.4f}%' for a,s in scores.items()]
    lines += ['', '## 평가 결과', '']
    lines += [f"- {a}: WAPE {r['overall']['WAPE']:.4f}%, BIAS {r['overall']['BIAS']:+.4f}%, {r['overall']['N']}행" for a,r in evaluations.items()]
    lines += ['', '서비스 모델은 변경하지 않았습니다. 평일 수는 실제 진료일 수가 아니며, 동종 품목 정보는 같은 월 자료의 동시 확보를 전제로 합니다.']
    (out/'결과요약.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def suite(args):
    out = args.output
    out.mkdir(parents=True,exist_ok=False)
    tasks = [('prepare',None,None)] + [('fit',a,f) for f in FOLDS for a in ARMS] + [('finalize',None,None)]
    for action, arm, fold in tasks:
        label = '_'.join(x for x in [action,fold,arm] if x)
        status(out,stage='running',task=label)
        command = [sys.executable,'-u',str(Path(__file__).resolve()),action,'--output',str(out),
                   '--source',str(args.source),'--reference',str(args.reference)]
        if arm:
            command += ['--arm',arm,'--fold',fold]
        with (out/f'{label}.log').open('w',encoding='utf-8') as log:
            child = subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
            began = time.monotonic()
            while child.poll() is None:
                reason = 'low_memory' if psutil.virtual_memory().available < 1.5*1024**3 else None
                if time.monotonic()-began > 7200:
                    reason = 'two_hour_task_limit'
                if reason:
                    p = psutil.Process(child.pid)
                    owned = p.children(recursive=True) + [p]
                    for proc in owned:
                        try:
                            proc.kill()
                        except psutil.NoSuchProcess:
                            pass
                    psutil.wait_procs(owned,timeout=10)
                    status(out,stage='paused',task=label,reason=reason)
                    return
                time.sleep(5)
        if child.returncode:
            status(out,stage='failed',task=label,returncode=child.returncode)
            return
        status(out,stage='task_completed',task=label)
        time.sleep(15)
    status(out,stage='completed')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action',choices=['suite','prepare','fit','finalize'])
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--arm',choices=list(ARMS))
    p.add_argument('--fold',choices=list(FOLDS))
    args = p.parse_args()
    try:
        globals()[args.action](args)
    except Exception:
        traceback.print_exc()
        raise
