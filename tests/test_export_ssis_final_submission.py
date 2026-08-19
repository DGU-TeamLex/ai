import unittest

from scripts.export_ssis_final_submission import EXPORT_ITEMS


class ExportSsisFinalSubmissionTest(unittest.TestCase):
    def test_export_items_have_unique_sources_and_korean_names(self):
        sources = [item.source.as_posix() for item in EXPORT_ITEMS]
        stems = [item.korean_stem for item in EXPORT_ITEMS]
        self.assertEqual(len(sources), len(set(sources)))
        self.assertEqual(len(stems), len(set(stems)))

    def test_research_synthesis_and_meta_code_audit_are_exported(self):
        sources = {item.source.as_posix() for item in EXPORT_ITEMS}
        self.assertIn(
            "docs/2026-08-19_03_RESEARCH_RESULT_SYNTHESIS.md", sources
        )
        self.assertIn(
            "outputs/meta_code_normalization_research_metrics.csv", sources
        )
        self.assertIn(
            "outputs/meta_code_normalization_research_audit.json", sources
        )


if __name__ == "__main__":
    unittest.main()
