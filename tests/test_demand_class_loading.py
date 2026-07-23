import json
from pathlib import Path
import tempfile
import unittest
import csv

import numpy as np
import pandas as pd

from src.loading.compute_demand_class_mu_corrected import (
    aggregate_institution_item_metrics,
    build_censored_metrics_from_raw,
    build_local_censored_metrics,
    build_quality_report,
    classify_and_correct_demand,
)
from src.loading.reflect_demand_class_mu_corrected import (
    load_institution_mapping,
    load_release_report,
    prepare_update_frame,
)
from src.data_loader import RAW_STOCK_COLUMNS


def raw_stock_row(date: str, closing: float, consumption: float) -> list[str]:
    values = {
        "부서코드": "D1",
        "물품코드": "ITEM1",
        "물품명": "ITEM ONE",
        "재고마감일": date,
        "이전최종재고량": "0",
        "마감재고량": str(closing),
        "구입처코드": "",
        "구입단가": "",
        "입고량": "0",
        "불출입고량": "0",
        "반납입고량": "0",
        "불출출고량": "0",
        "정상출고량": str(consumption),
        "반품출고량": "0",
        "폐기출고량": "0",
        "자동폐기출고량": "0",
        "보정출고량": "0",
        "보건기관코드_en": "A1234",
    }
    return [values[column] for column in RAW_STOCK_COLUMNS]


class CensoredDemandMetricsTest(unittest.TestCase):
    def test_partitioned_raw_reader_preserves_full_series(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw_dir = Path(temporary_directory) / "raw"
            raw_dir.mkdir()
            path = raw_dir / "stock.DAT"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(
                    file,
                    delimiter="|",
                    quotechar='"',
                    lineterminator="\n",
                )
                writer.writerow(RAW_STOCK_COLUMNS)
                writer.writerow(raw_stock_row("20250101", 10, 0))
                writer.writerow(raw_stock_row("20250111", 0, 5))
                writer.writerow(raw_stock_row("20250121", 5, 0))

            result = build_censored_metrics_from_raw(
                raw_dir=raw_dir,
                pattern="*.DAT",
                chunk_size=1,
                bucket_count=2,
                temp_dir=Path(temporary_directory),
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["total_days"], 21)
        self.assertEqual(result.iloc[0]["zero_stock_days"], 10)
        self.assertEqual(result.iloc[0]["held_stock_days"], 11)

    def test_zero_stock_duration_uses_days_between_transactions(self):
        source = pd.DataFrame(
            [
                {
                    "institution_code": "A1234",
                    "department": "D1",
                    "item_code": "ITEM1",
                    "closing_date": "2025-01-01",
                    "closing_stock": 10.0,
                    "consumption_qty": 0.0,
                },
                {
                    "institution_code": "A1234",
                    "department": "D1",
                    "item_code": "ITEM1",
                    "closing_date": "2025-01-11",
                    "closing_stock": 0.0,
                    "consumption_qty": 5.0,
                },
                {
                    "institution_code": "A1234",
                    "department": "D1",
                    "item_code": "ITEM1",
                    "closing_date": "2025-01-21",
                    "closing_stock": 5.0,
                    "consumption_qty": 0.0,
                },
            ]
        )

        result = build_local_censored_metrics(
            source,
            period_end=pd.Timestamp("2025-01-30"),
        ).iloc[0]

        self.assertEqual(result["total_days"], 30)
        self.assertEqual(result["zero_stock_days"], 10)
        self.assertEqual(result["held_stock_days"], 20)
        self.assertAlmostEqual(result["zero_ratio"], 1 / 3)
        self.assertAlmostEqual(result["mu_naive"], 5 / 30)
        self.assertAlmostEqual(result["mu_available_only_raw"], 5 / 20)

    def test_department_metrics_are_aggregated_before_classification(self):
        source = pd.DataFrame(
            [
                {
                    "institution_code": "A1234",
                    "department": "D1",
                    "item_code": "ITEM1",
                    "first_observation_date": pd.Timestamp("2025-01-01"),
                    "last_observation_date": pd.Timestamp("2025-01-30"),
                    "transaction_days": 3,
                    "demand_total": 10.0,
                    "total_days": 30,
                    "zero_stock_days": 10,
                    "held_stock_days": 20,
                    "unknown_stock_days": 0,
                    "negative_consumption_rows": 0,
                    "period_end": pd.Timestamp("2025-01-30"),
                },
                {
                    "institution_code": "A1234",
                    "department": "D2",
                    "item_code": "ITEM1",
                    "first_observation_date": pd.Timestamp("2025-01-01"),
                    "last_observation_date": pd.Timestamp("2025-01-30"),
                    "transaction_days": 2,
                    "demand_total": 5.0,
                    "total_days": 30,
                    "zero_stock_days": 20,
                    "held_stock_days": 10,
                    "unknown_stock_days": 0,
                    "negative_consumption_rows": 0,
                    "period_end": pd.Timestamp("2025-01-30"),
                },
            ]
        )

        result = aggregate_institution_item_metrics(source).iloc[0]

        self.assertEqual(result["department_count"], 2)
        self.assertEqual(result["total_days"], 60)
        self.assertEqual(result["zero_stock_days"], 30)
        self.assertEqual(result["demand_total"], 15.0)
        self.assertEqual(result["zero_ratio"], 0.5)

    def test_classification_uses_inventory_ratio_and_produces_finite_mu(self):
        rows = []
        for index, demand in enumerate([365.0, 400.0, 450.0, 500.0, 550.0], start=1):
            rows.append(
                {
                    "institution_code": f"A{index:04d}",
                    "item_code": "ITEM1",
                    "demand_total": demand,
                    "total_days": 730,
                    "zero_stock_days": 0,
                    "held_stock_days": 730,
                    "unknown_stock_days": 0,
                    "zero_ratio": 0.0,
                    "inventory_coverage": 1.0,
                    "mu_naive": demand / 730,
                }
            )
        rows.extend(
            [
                {
                    "institution_code": "B0001",
                    "item_code": "ITEM1",
                    "demand_total": 100.0,
                    "total_days": 730,
                    "zero_stock_days": 400,
                    "held_stock_days": 330,
                    "unknown_stock_days": 0,
                    "zero_ratio": 400 / 730,
                    "inventory_coverage": 1.0,
                    "mu_naive": 100 / 730,
                },
                {
                    "institution_code": "B0002",
                    "item_code": "ITEM1",
                    "demand_total": 0.0,
                    "total_days": 730,
                    "zero_stock_days": 0,
                    "held_stock_days": 730,
                    "unknown_stock_days": 0,
                    "zero_ratio": 0.0,
                    "inventory_coverage": 1.0,
                    "mu_naive": 0.0,
                },
                {
                    "institution_code": "B0003",
                    "item_code": "ITEM1",
                    "demand_total": 0.0,
                    "total_days": 730,
                    "zero_stock_days": 100,
                    "held_stock_days": 100,
                    "unknown_stock_days": 530,
                    "zero_ratio": 100 / 730,
                    "inventory_coverage": 200 / 730,
                    "mu_naive": 0.0,
                },
                {
                    "institution_code": "B0004",
                    "item_code": "ITEM1",
                    "demand_total": 0.0,
                    "total_days": 730,
                    "zero_stock_days": 400,
                    "held_stock_days": 330,
                    "unknown_stock_days": 0,
                    "zero_ratio": 400 / 730,
                    "inventory_coverage": 1.0,
                    "mu_naive": 0.0,
                },
            ]
        )

        result = classify_and_correct_demand(pd.DataFrame(rows))
        classes = result.set_index("institution_code")["demand_class"]

        self.assertEqual(classes["B0001"], "CENSORED")
        self.assertEqual(classes["B0002"], "DORMANT")
        self.assertEqual(classes["B0003"], "NOT_VERIFIED")
        self.assertEqual(classes["B0004"], "CENSORED")
        self.assertTrue(result["mu_corrected"].notna().all())
        self.assertTrue(result["mu_corrected"].ge(0).all())
        self.assertEqual(
            result.loc[result["institution_code"].eq("B0002"), "mu_corrected"].iloc[0],
            0.0,
        )
        self.assertTrue(
            result.loc[result["institution_code"].eq("B0004"), "review_required"].iloc[0]
        )
        positive_naive = result[result["mu_naive"].gt(0)]
        self.assertTrue(positive_naive["correction_factor"].le(10.0).all())

        local_for_report = pd.DataFrame(
            {"negative_consumption_rows": [0]}
        )
        report = build_quality_report(local_for_report, result)
        self.assertEqual(report["zero_ratio_definition"], "zero_stock_duration_days / total_duration_days")
        self.assertFalse(report["status_update_included"])
        self.assertEqual(report["review_load_overlap_count"], 0)
        self.assertTrue(report["review_rows_excluded_from_load"])


class DemandClassReflectionGuardTest(unittest.TestCase):
    def test_explicit_mapping_and_update_frame_do_not_touch_status(self):
        handoff = pd.DataFrame(
            [
                {
                    "anon_institution_code": "A1234",
                    "standard_code": "ITEM1",
                    "demand_class": "DORMANT",
                    "mu_corrected": 0.0,
                    "review_required": False,
                    "load_eligible": True,
                }
            ]
        )
        mapping = pd.DataFrame(
            [
                {
                    "anon_institution_code": "A1234",
                    "institution_id": "inst_0001",
                }
            ]
        )

        result = prepare_update_frame(handoff, mapping)

        self.assertEqual(
            result.columns.tolist(),
            ["institution_id", "standard_code", "demand_class", "mu_corrected"],
        )
        self.assertNotIn("status", result.columns)

    def test_legacy_sorted_zip_is_blocked_and_count_mismatch_always_raises(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "institution_ids_sorted.csv"
            pd.DataFrame(
                {"institution_id": ["inst_0001", "inst_0002"]}
            ).to_csv(path, index=False)

            with self.assertRaisesRegex(ValueError, "unsafe sorted-zip"):
                load_institution_mapping(path, ["A1234"])
            with self.assertRaisesRegex(ValueError, "count mismatch"):
                load_institution_mapping(
                    path,
                    ["A1234"],
                    allow_legacy_sorted_zip=True,
                )

    def test_non_releasable_quality_report_blocks_loading(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "report.json"
            path.write_text(
                json.dumps(
                    {
                        "quality_status": "REVIEW",
                        "batch_release_allowed": False,
                        "status_update_included": False,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "not releasable"):
                load_release_report(path)

    def test_report_with_status_update_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "report.json"
            path.write_text(
                json.dumps(
                    {
                        "quality_status": "PASS",
                        "batch_release_allowed": True,
                        "status_update_included": True,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "must not include"):
                load_release_report(path)

    def test_non_finite_mu_is_rejected_before_database_loading(self):
        handoff = pd.DataFrame(
            [
                {
                    "anon_institution_code": "A1234",
                    "standard_code": "ITEM1",
                    "demand_class": "ACTIVE",
                    "mu_corrected": np.inf,
                    "review_required": False,
                    "load_eligible": True,
                }
            ]
        )
        mapping = pd.DataFrame(
            [
                {
                    "anon_institution_code": "A1234",
                    "institution_id": "inst_0001",
                }
            ]
        )

        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            prepare_update_frame(handoff, mapping)


if __name__ == "__main__":
    unittest.main()
