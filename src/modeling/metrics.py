import numpy as np
import pandas as pd


def regression_metrics(y_true, y_pred) -> dict:
    y_true = np.asarray(y_true, dtype="float64")
    y_pred = np.asarray(y_pred, dtype="float64")
    finite = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true = y_true[finite]
    y_pred = y_pred[finite]
    if len(y_true) == 0:
        return {
            "N": 0,
            "ACTUAL_SUM": None,
            "PREDICTION_SUM": None,
            "MAE": None,
            "RMSE": None,
            "RMSLE": None,
            "MAPE": None,
            "SMAPE": None,
            "WAPE": None,
            "BIAS": None,
            "BIAS_PCT": None,
            "ZERO_ACTUAL_RATE": None,
        }

    error = y_true - y_pred
    abs_error = np.abs(error)
    nonzero = y_true != 0
    smape_denominator = np.abs(y_true) + np.abs(y_pred)
    smape_mask = smape_denominator != 0
    actual_sum = np.abs(y_true).sum()
    prediction_sum = y_pred.sum()
    signed_bias = prediction_sum - y_true.sum()

    return {
        "N": int(len(y_true)),
        "ACTUAL_SUM": float(y_true.sum()),
        "PREDICTION_SUM": float(prediction_sum),
        "MAE": float(np.mean(abs_error)),
        "RMSE": float(np.sqrt(np.mean(error**2))),
        "RMSLE": float(
            np.sqrt(
                np.mean(
                    (np.log1p(np.clip(y_true, 0, None)) - np.log1p(np.clip(y_pred, 0, None)))
                    ** 2
                )
            )
        ),
        "MAPE": float(np.mean(abs_error[nonzero] / np.abs(y_true[nonzero])) * 100)
        if np.any(nonzero)
        else None,
        "SMAPE": float(np.mean(2 * abs_error[smape_mask] / smape_denominator[smape_mask]) * 100)
        if np.any(smape_mask)
        else None,
        "WAPE": float(abs_error.sum() / actual_sum * 100)
        if actual_sum != 0
        else None,
        "BIAS": float(np.mean(y_pred - y_true)),
        "BIAS_PCT": float(signed_bias / actual_sum * 100) if actual_sum != 0 else None,
        "ZERO_ACTUAL_RATE": float(np.mean(y_true == 0) * 100),
    }


def metrics_by_group(
    df: pd.DataFrame,
    group_cols: list[str],
    pred_col: str,
    actual_col: str = "actual_usage",
) -> pd.DataFrame:
    rows = []
    for keys, group in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        row.update(regression_metrics(group[actual_col], group[pred_col]))
        row["n_rows"] = len(group)
        rows.append(row)
    return pd.DataFrame(rows)
