import unittest

import pandas as pd

from src.modeling.data_quality import (
    STANDARDIZATION_COLUMNS,
    _resolve_alias_duplicates,
)


def alias_row(key: str, status: str, group: str, family: str = "") -> dict:
    return {
        "local_item_key": key,
        "normalization_status": status,
        "item_group_id_candidate": group,
        "item_family_id_candidate": family,
    }


class ResolveAliasDuplicatesTest(unittest.TestCase):
    def test_unique_input_passes_through(self):
        aliases = pd.DataFrame(
            [
                alias_row("I1::A", "family_candidate", "MED_SUPPLY", "MEDICAL_MASK"),
                alias_row("I1::B", "unresolved", "UNCLASSIFIED"),
            ]
        )

        result = _resolve_alias_duplicates(aliases)

        self.assertEqual(len(result), 2)
        self.assertFalse(result["local_item_key"].duplicated().any())

    def test_rows_identical_in_every_read_column_collapse(self):
        row = alias_row("I1::A", "group_candidate", "LAB_REAGENT")
        aliases = pd.DataFrame([row, dict(row)])

        result = _resolve_alias_duplicates(aliases)

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["item_group_id_candidate"], "LAB_REAGENT")

    def test_classified_row_wins_over_unresolved_placeholder(self):
        aliases = pd.DataFrame(
            [
                alias_row("I1::A", "unresolved", "UNCLASSIFIED"),
                alias_row(
                    "I1::A", "family_candidate", "LAB_REAGENT", "BLOOD_GLUCOSE_TEST_STRIP"
                ),
            ]
        )

        result = _resolve_alias_duplicates(aliases)

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["normalization_status"], "family_candidate")
        self.assertEqual(
            result.iloc[0]["item_family_id_candidate"], "BLOOD_GLUCOSE_TEST_STRIP"
        )

    def test_two_different_classifications_still_raise(self):
        aliases = pd.DataFrame(
            [
                alias_row("I1::A", "family_candidate", "MED_SUPPLY", "MEDICAL_MASK"),
                alias_row(
                    "I1::A", "family_candidate", "LAB_REAGENT", "BLOOD_GLUCOSE_TEST_STRIP"
                ),
            ]
        )

        with self.assertRaises(ValueError) as caught:
            _resolve_alias_duplicates(aliases)

        self.assertIn("conflicting classifications", str(caught.exception))

    def test_unresolved_only_duplicates_collapse_to_one(self):
        aliases = pd.DataFrame(
            [
                alias_row("I1::A", "unresolved", "UNCLASSIFIED"),
                alias_row("I1::A", "unresolved", "UNCLASSIFIED", "SOMETHING"),
            ]
        )

        # Both rows are placeholders, so neither carries identity, but they are
        # not byte-identical. Dropping both would lose the key entirely, so one
        # placeholder has to survive.
        result = _resolve_alias_duplicates(aliases)

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["local_item_key"], "I1::A")
        self.assertEqual(result.iloc[0]["normalization_status"], "unresolved")

    def test_columns_are_preserved(self):
        aliases = pd.DataFrame(
            [
                alias_row("I1::A", "unresolved", "UNCLASSIFIED"),
                alias_row("I1::A", "group_candidate", "MED_TOPICAL"),
            ]
        )

        result = _resolve_alias_duplicates(aliases)

        for column in ["local_item_key", *STANDARDIZATION_COLUMNS]:
            self.assertIn(column, result.columns)


if __name__ == "__main__":
    unittest.main()
