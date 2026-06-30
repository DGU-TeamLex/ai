import pandas as pd

from .config import EVALUATION_REPORT_PATH, OUTPUT_DIR, PREDICTION_PATH
from .metrics import regression_metrics
from .utils import ensure_dirs, setup_logging


def build_evaluation_report(predictions: pd.DataFrame) -> pd.DataFrame:
    pred_cols = [col for col in predictions.columns if col.endswith("_pred") or col == "predicted_usage"]
    rows = []
    for pred_col in pred_cols:
        row = {"model": pred_col}
        row.update(regression_metrics(predictions["actual_usage"], predictions[pred_col]))
        rows.append(row)
    return pd.DataFrame(rows).sort_values("WAPE", na_position="last")


def run_evaluation() -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    predictions = pd.read_csv(PREDICTION_PATH)
    report = build_evaluation_report(predictions)
    report.to_csv(EVALUATION_REPORT_PATH, index=False)


if __name__ == "__main__":
    run_evaluation()

