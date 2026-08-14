import unittest

import pandas as pd

from src.modeling.standardized_history import (
    _resolve_candidates,
    attach_standard_item_features,
)


def candidate(
    period: str,
    local_key: str,
    representative_id: str,
    core: str,
) -> dict[str, object]:
    return {
        "data_period": period,
        "local_item_key": local_key,
        "raw_item_name": local_key,
        "product_name_candidate": local_key,
        "representative_item_id": representative_id,
        "match_name_core": core,
        "fallback_group_id": "LAB_REAGENT",
        "fallback_family_id": "BLOOD_GLUCOSE_TEST_STRIP",
        "fallback_subtype_id": "BLOOD_GLUCOSE_TEST_STRIP",
        "fallback_specification": "",
        "fallback_unit_code": "EA",
    }


class StandardizedHistoryTest(unittest.TestCase):
    def setUp(self):
        self.integrated = pd.DataFrame(
            [
                {
                    "representative_item_id": "ITEM_CURRENT",
                    "representative_name": "혈당스틱",
                    "match_name_core": "혈당스틱",
                    "resolved_standard_item_key": "NAME::ITEM_CURRENT",
                    "resolved_definition_key": (
                        "DEF::BLOOD_GLUCOSE_TEST_STRIP::"
                        "BLOOD_GLUCOSE_TEST_STRIP::UNSPECIFIED_SPEC::EA"
                    ),
                    "resolved_group_id": "LAB_REAGENT",
                    "resolved_family_id": "BLOOD_GLUCOSE_TEST_STRIP",
                    "resolved_subtype_id": "BLOOD_GLUCOSE_TEST_STRIP",
                    "resolved_specification": "",
                    "resolved_unit_code": "EA",
                    "semantic_definition_applied": True,
                }
            ]
        )

    def test_strict_and_unique_core_matches_share_current_standard_name(self):
        candidates = pd.DataFrame(
            [
                candidate(
                    "current",
                    "I1::C1",
                    "ITEM_CURRENT",
                    "혈당스틱",
                ),
                candidate(
                    "historical",
                    "I1::C2",
                    "ITEM_CURRENT",
                    "혈당스틱",
                ),
                candidate(
                    "historical",
                    "I2::C3",
                    "ITEM_HISTORICAL_ALIAS",
                    "혈당스틱",
                ),
            ]
        )

        mapping = _resolve_candidates(candidates, self.integrated)

        self.assertEqual(mapping["standard_item_key"].nunique(), 1)
        self.assertTrue(
            mapping["standard_item_definition_key"]
            .str.startswith("DEF::")
            .all()
        )
        self.assertTrue(mapping["historical_training_eligible"].all())

    def test_historical_only_name_is_audited_but_not_trained(self):
        mapping = _resolve_candidates(
            pd.DataFrame(
                [
                    candidate(
                        "historical",
                        "I2::NEW",
                        "ITEM_HISTORY_ONLY",
                        "과거전용품목",
                    )
                ]
            ),
            self.integrated,
        )

        row = mapping.iloc[0]
        self.assertEqual(
            row["standard_item_key"],
            "NAME::ITEM_HISTORY_ONLY",
        )
        self.assertEqual(
            row["standardization_match_method"],
            "historical_name_fallback",
        )
        self.assertFalse(row["historical_training_eligible"])

    def test_mapping_join_preserves_physical_stock_rows(self):
        monthly = pd.DataFrame(
            {
                "data_period": ["current", "current"],
                "institution_code": ["I1", "I2"],
                "item_code": ["C1", "C2"],
                "consumption_qty": [2.0, 3.0],
            }
        )
        mapping = _resolve_candidates(
            pd.DataFrame(
                [
                    candidate(
                        "current",
                        "I1::C1",
                        "ITEM_CURRENT",
                        "혈당스틱",
                    ),
                    candidate(
                        "current",
                        "I2::C2",
                        "ITEM_CURRENT",
                        "혈당스틱",
                    ),
                ]
            ),
            self.integrated,
        )

        result = attach_standard_item_features(monthly, mapping)

        self.assertEqual(len(result), 2)
        self.assertEqual(result["consumption_qty"].sum(), 5.0)
        self.assertEqual(result["standard_item_key"].nunique(), 1)


if __name__ == "__main__":
    unittest.main()
