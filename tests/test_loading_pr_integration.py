import csv
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.loading.apply_inventory_policy import (
    UPDATE_SQL,
    prepare_handoff,
    validate_restricted_update_sql,
)
from src.loading.apply_is_medical import classify_is_medical
from src.loading.build_ingredient_tiers import assign_tiers, build_master
from src.loading.compute_demand_quantity_distribution import quantiles
from src.loading.compute_zero_stock_reason import build_zero_stock_reasons
from src.loading.compute_zero_stock_reason import (
    ZERO_STOCK_POLICY_VERSION,
    load_institution_mapping,
)
from src.loading.load_inventory_daily import COLMAP, rows_from


class LoadingPullRequestIntegrationTest(unittest.TestCase):
    def test_status_exclusion_sql_is_dry_run_by_default_and_keeps_audit(self):
        sql = (
            Path("src/loading/apply_status_exclusion.sql")
            .read_text(encoding="utf-8")
            .lower()
        )

        self.assertIn("\\set apply 0", sql)
        self.assertIn("rollback;", sql)
        self.assertIn("inventory_status_change_audit", sql)
        self.assertIn(ZERO_STOCK_POLICY_VERSION, sql)
        self.assertNotIn("drop table if exists _bak_status", sql)

    def test_sorted_institution_mapping_fails_when_cardinality_differs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "institution_ids.csv"
            pd.DataFrame(
                {"institution_id": ["inst_1", "inst_2"]}
            ).to_csv(path, index=False)

            with self.assertRaisesRegex(ValueError, "unsafe"):
                load_institution_mapping(
                    pd.Series(["anon_1"]),
                    explicit_mapping_path="",
                    institution_ids_path=path,
                )

    def test_zero_stock_reason_uses_all_physical_inbound(self):
        ledger = pd.DataFrame(
            [
                {
                    "보건기관코드_en": "A",
                    "물품코드": "TRANSFER_OK",
                    "재고마감일": "2026-06-01",
                    "정상출고량": 4,
                    "마감재고량": 0,
                    "이전최종재고량": 1,
                    "입고량": 0,
                    "불출입고량": 3,
                    "반납입고량": 0,
                },
                {
                    "보건기관코드_en": "A",
                    "물품코드": "RETURN_OK",
                    "재고마감일": "2026-07-01",
                    "정상출고량": 4,
                    "마감재고량": 0,
                    "이전최종재고량": 1,
                    "입고량": 0,
                    "불출입고량": 0,
                    "반납입고량": 3,
                },
                {
                    "보건기관코드_en": "A",
                    "물품코드": "MISSING",
                    "재고마감일": "2026-07-01",
                    "정상출고량": 4,
                    "마감재고량": 0,
                    "이전최종재고량": 1,
                    "입고량": 1,
                    "불출입고량": 0,
                    "반납입고량": 0,
                },
                {
                    "보건기관코드_en": "A",
                    "물품코드": "DORMANT",
                    "재고마감일": "2026-07-01",
                    "정상출고량": -2,
                    "마감재고량": 0,
                    "이전최종재고량": 0,
                    "입고량": 0,
                    "불출입고량": 0,
                    "반납입고량": 0,
                },
            ]
        )

        result = build_zero_stock_reasons(ledger).set_index("물품코드")

        self.assertEqual(
            result.loc["TRANSFER_OK", "zero_stock_reason"],
            "TRUE_STOCKOUT",
        )
        self.assertEqual(
            result.loc["RETURN_OK", "zero_stock_reason"],
            "TRUE_STOCKOUT",
        )
        self.assertEqual(
            result.loc["MISSING", "zero_stock_reason"],
            "DATA_MISSING",
        )
        self.assertEqual(
            result.loc["DORMANT", "zero_stock_reason"],
            "NOT_OPERATED",
        )
        self.assertEqual(result.loc["TRANSFER_OK", "recent_demand"], 4)

    def test_inventory_handoff_cannot_write_unresolved_policy_columns(self):
        frame = pd.DataFrame(
            [
                {
                    "institution_id": "inst_1",
                    "standard_code": "item_1",
                    "on_hand": 3,
                    "mu": 1.5,
                    "sigma": 0.2,
                    "mu_forecast": 1.2,
                    "demand_class": "ACTIVE",
                    "zero_stock_reason": "IN_STOCK",
                    "order_suppress_reason": "",
                }
            ]
        )

        prepared = prepare_handoff(frame)
        validate_restricted_update_sql()

        self.assertEqual(len(prepared), 1)
        with self.assertRaisesRegex(ValueError, "ss"):
            validate_restricted_update_sql(
                UPDATE_SQL.replace("updated_at = now()", "ss = 1")
            )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            prepare_handoff(pd.concat([frame, frame], ignore_index=True))

    def test_drug_ingredient_evidence_overrides_nonmedical_name_tier(self):
        self.assertTrue(
            classify_is_medical(
                raw_material_meta_code="PROMO_MATERIAL",
                standard_name="홍보용 약품",
                has_drug_ingredient_evidence=True,
            )
        )
        self.assertFalse(
            classify_is_medical(
                raw_material_meta_code="PROMO_MATERIAL",
                standard_name="홍보용 볼펜",
                has_drug_ingredient_evidence=False,
            )
        )
        self.assertTrue(
            classify_is_medical(
                raw_material_meta_code="CARRIER_BAG",
                standard_name="의료폐기물 봉투",
                has_drug_ingredient_evidence=False,
            )
        )

    def test_ingredient_votes_are_distinct_by_institution_and_ambiguous_is_not_tier1(self):
        header = [
            "보건기관코드_en",
            "약품코드",
            "약품명1",
            "용도구분",
            "약종류구분",
            "약품단위1",
            "성분코드",
            "성분명",
        ]
        rows = [
            ["A", "D1", "약1", "진료", "경구", "정", "I1", "성분1"],
            ["A", "D1", "약1", "진료", "경구", "정", "I1", "성분1"],
            ["B", "D1", "약1", "진료", "경구", "정", "I1", "성분1"],
            ["A", "D2", "약2", "진료", "경구", "정", "I1", "성분1"],
            ["B", "D2", "약2", "진료", "경구", "정", "I1", "성분1"],
            ["A", "D3", "복합\n약", "진료", "경구", "정", "I2", "성분2"],
            ["B", "D3", "복합\n약", "진료", "경구", "정", "I3", "성분3"],
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "drug.DAT"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(
                    file,
                    delimiter="|",
                    quotechar='"',
                    lineterminator="\n",
                )
                writer.writerow(header)
                writer.writerows(rows)
            master, read_rows = build_master(str(path))

        tiers, _ = assign_tiers(master, {})
        self.assertEqual(read_rows, len(rows))
        self.assertEqual(master["D1"]["source_institution_count"], 2)
        self.assertEqual(master["D1"]["ingredient_consensus_ratio"], 1.0)
        self.assertEqual(tiers["D1"], (1, "ING:I1"))
        self.assertEqual(master["D3"]["has_multiple_ingredients"], "true")
        self.assertEqual(tiers["D3"], (2, "USE:진료/경구"))

    def test_quantity_quantiles_preserve_fractional_values(self):
        result = quantiles(
            [(0.5, 1), (1.0, 1), (2.0, 2)],
            4,
            [0.25, 0.5, 0.75],
        )
        self.assertEqual(result, {0.25: 0.5, 0.5: 1.0, 0.75: 2.0})

    def test_daily_loader_rejects_invalid_calendar_date(self):
        header = [source for source, _ in COLMAP[:15]]
        valid = {
            source: "0"
            for source in header
        }
        valid.update(
            {
                "보건기관코드_en": "A",
                "부서코드": "D",
                "물품코드": "I",
                "재고마감일": "20261301",
            }
        )
        stats = {
            "read": 0,
            "bad_cols": 0,
            "bad_key": 0,
            "neg_closing": 0,
            "neg_prev": 0,
            "identity_checked": 0,
            "identity_bad": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock.DAT"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter="|")
                writer.writerow(header)
                writer.writerow([valid[column] for column in header])
            parsed = list(rows_from(path, stats))

        self.assertEqual(parsed, [])
        self.assertEqual(stats["bad_key"], 1)


if __name__ == "__main__":
    unittest.main()
