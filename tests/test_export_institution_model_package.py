import gzip
from pathlib import Path

import pandas as pd

from scripts.export_institution_model_package import (
    UTF8_BOM,
    csv_to_csv_gz,
    describe_column,
    parquet_to_csv_gz,
)


def test_streamed_csv_exports_preserve_rows_and_utf8_bom(tmp_path: Path):
    frame = pd.DataFrame(
        {
            "institution_code": ["D0001", "D0002"],
            "raw_item_name": ["일회용 주사기", "의료용 장갑"],
            "demand_qty": [10.0, 0.0],
        }
    )
    parquet = tmp_path / "input.parquet"
    source_csv = tmp_path / "output.csv"
    frame.to_parquet(parquet, index=False)
    frame.to_csv(source_csv, index=False, encoding="utf-8")

    parquet_gz = tmp_path / "parquet.csv.gz"
    csv_gz = tmp_path / "source.csv.gz"
    assert parquet_to_csv_gz(parquet, parquet_gz, batch_size=1) == (2, 3)
    assert csv_to_csv_gz(source_csv, csv_gz) == (2, 3)

    for exported in [parquet_gz, csv_gz]:
        with gzip.open(exported, "rb") as source:
            assert source.read(3) == UTF8_BOM
        restored = pd.read_csv(exported)
        assert restored["raw_item_name"].tolist() == ["일회용 주사기", "의료용 장갑"]


def test_codebook_describes_high_value_columns():
    assert "익명화" in describe_column("모델입력", "institution_code")
    assert "안전재고" in describe_column("모델출력", "safety_stock")
    assert "예측값" in describe_column("모델출력", "stock_model_a_usage_only_pred")
