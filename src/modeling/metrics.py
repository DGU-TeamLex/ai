import numpy as np
import pandas as pd


def regression_metrics(y_true, y_pred) -> dict:
    y_true = np.asarray(y_true, dtype="float64")
    y_pred = np.asarray(y_pred, dtype="float64")
    error = y_true - y_pred
    abs_error = np.abs(error)
    nonzero = y_true != 0
    smape_denominator = np.abs(y_true) + np.abs(y_pred)
    smape_mask = smape_denominator != 0

    return {
        "MAE": float(np.mean(abs_error)),
        "RMSE": float(np.sqrt(np.mean(error**2))),
        "MAPE": float(np.mean(abs_error[nonzero] / np.abs(y_true[nonzero])) * 100)
        if np.any(nonzero)
        else None,
        "SMAPE": float(np.mean(2 * abs_error[smape_mask] / smape_denominator[smape_mask]) * 100)
        if np.any(smape_mask)
        else None,
        "WAPE": float(abs_error.sum() / np.abs(y_true).sum() * 100)
        if np.abs(y_true).sum() != 0
        else None,
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

