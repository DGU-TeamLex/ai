import unittest

import pandas as pd

from scripts.analysis.item_criticality_abc import (
    build_item_importance_table,
)


class ItemCriticalityVolumeClassTest(unittest.TestCase):
    def test_representative_axis_merges_only_approved_local_items(self):
        ledger = pd.DataFrame(
            [
                {
                    "보건기관코드_en": "INST_A",
                    "물품코드": "USE001",
                    "물품명": "주사기 3mL",
                    "정상출고량": 10.0,
                    "구입단가": 100.0,
                },
                {
                    "보건기관코드_en": "INST_B",
                    "물품코드": "USE777",
                    "물품명": "주사기 3mL",
                    "정상출고량": 20.0,
                    "구입단가": 110.0,
                },
                {
                    "보건기관코드_en": "INST_C",
                    "물품코드": "USE001",
                    "물품명": "전혀 다른 지역 물품",
                    "정상출고량": 5.0,
                    "구입단가": None,
                },
            ]
        )
        links = pd.DataFrame(
            [
                {
                    "local_item_key": "INST_A::USE001",
                    "representative_item_id": "REP_SYRINGE",
                },
                {
                    "local_item_key": "INST_B::USE777",
                    "representative_item_id": "REP_SYRINGE",
                },
            ]
        )

        result = build_item_importance_table(ledger, links)

        syringe = result[
            result["analysis_item_key"].eq("REP_SYRINGE")
        ].iloc[0]
        unresolved = result[
            result["analysis_item_key"].str.startswith("UNRESOLVED_LOCAL")
        ].iloc[0]
        self.assertEqual(syringe["usage_qty"], 30.0)
        self.assertEqual(unresolved["usage_qty"], 5.0)
        self.assertTrue(unresolved["is_unresolved_local"])
        self.assertIn("volume_class", result.columns)
        self.assertNotIn("abc_class", result.columns)


if __name__ == "__main__":
    unittest.main()
