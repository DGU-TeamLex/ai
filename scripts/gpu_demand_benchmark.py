"""Isolated retrospective GPU benchmark; never updates serving artifacts.

Training uses targets through July 2025; validation August-September;
test October-December. Existing test outcomes are already known to the team.
This is NOT a fresh holdout or evidence for deployment.
"""
import argparse
import gc
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb


FEATURES = [
    'month', 'quarter', 'history_months', 'series_observation_count',
    'lag_1', 'lag_2', 'lag_3', 'lag_6', 'lag_12',
    'rolling_mean_3', 'rolling_std_3', 'rolling_mean_6', 'rolling_std_6',
    'rolling_mean_12', 'rolling_std_12', 'rolling_median_3',
    'expanding_mean', 'zero_rate_6', 'zero_rate_12',
    'inbound_qty_lag_1', 'month_end_stock_lag_1', 'stockout_rate_lag_1',
    'disposal_qty_lag_1', 'is_winter', 'is_summer',
]
KEYS = ['forecast_origin_month', 'institution_code', 'department', 'item_code']


def metrics(actual, prediction):
    actual = np.asarray(actual, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    if not len(actual) or not np.isfinite(actual).all() or not np.isfinite(prediction).all():
        raise ValueError('Empty or nonfinite evaluation')
    total = actual.sum()
    if total <= 0:
        raise ValueError('WAPE denominator must be positive')
    error = prediction - actual
    return dict(N=len(actual), WAPE=float(100 * np.abs(error).sum() / total),
                BIAS_PCT=float(100 * error.sum() / total))


def split_masks(frame):
    target_month = frame['forecast_month']
    origin = frame['year_month']
    historical = origin.between('2018-01-01', '2019-12-01') & frame['historical_training_eligible'].fillna(False)
    current = origin.between('2024-01-01', '2025-06-01')
    return (historical | current,
            target_month.between('2025-08-01', '2025-09-01'),
            target_month.between('2025-10-01', '2025-12-01'))


def run(args):
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)  # Never overwrite an earlier run.
    started = time.time()
    def status(stage, **extra):
        payload = dict(stage=stage, elapsed_seconds=time.time()-started, **extra)
        (out / 'status.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
        print(json.dumps(payload), flush=True)
    try:
        status('loading')
        frame = pd.read_parquet(Path(args.source) / 'outputs/stock_feature_table.parquet',
            columns=FEATURES + ['year_month', 'forecast_month', 'target_usage',
                'historical_training_eligible', 'institution_code', 'department', 'item_code'],
            filters=[('target_usage', '>=', 0), ('lag_1', '>=', 0)])
        train, valid, test = split_masks(frame)
        if not all(mask.any() for mask in (train, valid, test)):
            raise ValueError('Empty time split')
        policy = json.loads((Path(args.source) / 'data/mapping/historical_training_policy.json').read_text(encoding='utf-8'))
        weight = np.where(frame.loc[train, 'year_month'] < pd.Timestamp('2020-01-01'),
                          policy['selected_historical_weight'], 1).astype('float32')
        def matrix(mask):
            return frame.loc[mask, FEATURES].astype('float32').replace([np.inf, -np.inf], np.nan)
        xt, xv, xe = matrix(train), matrix(valid), matrix(test)
        yt = frame.loc[train, 'target_usage'].to_numpy(dtype='float32')
        yv = frame.loc[valid, 'target_usage'].to_numpy(dtype='float32')
        result = frame.loc[test, ['year_month', 'institution_code', 'department', 'item_code', 'target_usage']].copy()
        result.rename(columns={'year_month':'forecast_origin_month'}, inplace=True)
        for key in KEYS[1:]:
            result[key] = result[key].astype(str)
        del frame
        gc.collect()
        rows = []
        for name, objective in [('xgb_squared', 'reg:squarederror'), ('xgb_tweedie', 'reg:tweedie')]:
            status('training', model=name, train_rows=len(xt), validation_rows=len(xv), test_rows=len(xe))
            model = xgb.XGBRegressor(objective=objective, device='cuda', tree_method='hist',
                n_estimators=400, max_depth=5, max_bin=64, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, n_jobs=2, random_state=42,
                early_stopping_rounds=40, eval_metric='mae')
            model.fit(xt, yt, sample_weight=weight, eval_set=[(xv, yv)], verbose=False)
            config = json.loads(model.get_booster().save_config())
            if config['learner']['generic_param']['device'] != 'cuda:0':
                raise RuntimeError('GPU fallback detected')
            vp = np.maximum(model.predict(xv), 0)
            ep = np.maximum(model.predict(xe), 0)
            # Calibration uses validation only; never test actuals.
            scale = float(np.clip(yv.astype(float).sum() / max(vp.astype(float).sum(), 1e-8), .8, 1.2))
            result[name] = ep
            result[name + '_calibrated'] = ep * scale
            for suffix, factor in [('', 1), ('_calibrated', scale)]:
                rows.append(dict(model=name+suffix, scale=factor, best_iteration=model.best_iteration,
                                 **{'validation_'+k:v for k,v in metrics(yv, vp*factor).items()}))
            model.save_model(out / (name + '.ubj'))
            result.to_parquet(out / 'predictions_checkpoint.parquet', index=False)
            status('model_completed', model=name)
            del model
            gc.collect()
            time.sleep(15)
        baseline_columns = ['stock_model_a_usage_only_pred', 'stock_model_a_usage_tweedie_pred',
                            'baseline_rolling_mean_3_pred', 'temporal_ensemble_pred']
        baseline = pd.read_csv(Path(args.source) / 'outputs/stock_backtest_predictions.csv',
            usecols=KEYS+['actual_usage']+baseline_columns, dtype={k:str for k in KEYS[1:]},
            parse_dates=['forecast_origin_month'])
        merged = result.merge(baseline, on=KEYS, how='inner', validate='one_to_one')
        if len(merged) != len(result):
            raise ValueError(f'Baseline row coverage mismatch: {len(merged)}/{len(result)}')
        if not np.allclose(merged.target_usage, merged.actual_usage):
            raise ValueError('Target alignment mismatch')
        merged['fixed_blend_50_50'] = (merged[baseline_columns[0]]+merged[baseline_columns[1]])/2
        for row in rows:
            row.update({'test_'+k:v for k,v in metrics(merged.actual_usage, merged[row['model']]).items()})
        for name in baseline_columns+['fixed_blend_50_50']:
            rows.append(dict(model=name, **{'test_'+k:v for k,v in metrics(merged.actual_usage, merged[name]).items()}))
        pd.DataFrame(rows).to_csv(out / 'comparison.csv', index=False, encoding='utf-8-sig')
        merged.to_parquet(out / 'predictions.parquet', index=False)
        (out / 'audit.json').write_text(json.dumps(dict(features=FEATURES, train_rows=len(xt),
            validation_rows=len(xv), test_rows=len(merged), historical_weight=policy['selected_historical_weight'],
            xgboost_version=xgb.__version__, source=str(Path(args.source).resolve()),
            limitations=['Retrospective reused test, not a new holdout',
                         'Numeric features only; no categorical identities or external risk',
                         'Comparison changes model and feature set, not GPU-only causal effect',
                         'Validation calibration can trade WAPE against absolute bias',
                         '400 rounds and one seed per objective; not exhaustive tuning'],
            serving_changed=False), indent=2), encoding='utf-8')
        status('completed', results=rows)
    except Exception as exc:
        status('failed', error=repr(exc))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    run(parser.parse_args())
