import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from src.news.news_risk_scorer import build_news_risk_outputs
from src.news.news_collector import collect_gdelt_news, collect_news, load_news_csv
from src.news.news_llm_analyzer import analyze_news_row


MAPPING = pd.DataFrame(
    [
        {
            "stock_item_key": "INST::DEPT::RESP1",
            "item_code": "RESP1",
            "item_name": "호흡기 물품",
            "related_material": "respiratory disease",
            "demand_risk_meta_code": "RESPIRATORY_INFECTIOUS_DISEASE",
            "mapping_weight": 1.0,
        },
        {
            "stock_item_key": "INST::DEPT::LATEX1",
            "item_code": "LATEX1",
            "item_name": "의료용 장갑",
            "related_material": "latex",
            "mapping_weight": 1.0,
        },
    ]
)

COUNTRY_WEIGHTS = pd.DataFrame(
    [
        {"country": "Korea", "region_weight": 1.0},
        {"country": "Unknown", "region_weight": 0.5},
    ]
)


def infectious_news(date: str, article_id: str, cluster_id: str | None = None) -> dict:
    row = {
        "article_id": article_id,
        "date": date,
        "title": "독감 확산으로 호흡기 의료물품 수요 증가",
        "summary": "감염병 확산이 이어지고 있다.",
        "source": "WHO",
        "country": "Korea",
        "url": f"test://{article_id}",
    }
    if cluster_id:
        row["event_cluster_id"] = cluster_id
    return row


class NewsRiskScorerTest(unittest.TestCase):
    def test_unique_article_has_full_novelty_and_only_matches_related_items(self):
        scores, audit = build_news_risk_outputs(
            news=pd.DataFrame([infectious_news("2024-01-10", "a1")]),
            mapping=MAPPING,
            country_weights=COUNTRY_WEIGHTS,
        )

        self.assertEqual(set(audit["stock_item_key"]), {"INST::DEPT::RESP1"})
        self.assertEqual(float(audit.iloc[0]["novelty_weight"]), 1.0)
        self.assertEqual(int(audit.iloc[0]["duplicate_count"]), 0)
        self.assertEqual(audit.iloc[0]["approved_mapping_path"], "demand")
        self.assertEqual(scores.iloc[0]["STD_YYYYMM"], "2024-01")
        self.assertTrue(scores.iloc[0]["has_approved_demand_mapping"])
        self.assertFalse(scores.iloc[0]["has_approved_material_mapping"])

    def test_duplicate_articles_share_cluster_penalty(self):
        news = pd.DataFrame(
            [
                infectious_news("2024-01-10", "a1", "flu-cluster"),
                infectious_news("2024-01-10", "a2", "flu-cluster"),
            ]
        )
        _, audit = build_news_risk_outputs(news=news, mapping=MAPPING, country_weights=COUNTRY_WEIGHTS)

        self.assertTrue((audit["duplicate_count"] == 1).all())
        self.assertTrue(
            all(math.isclose(value, 1 / math.sqrt(2)) for value in audit["novelty_weight"])
        )

    def test_future_month_news_does_not_change_historical_recency(self):
        january = pd.DataFrame([infectious_news("2024-01-10", "jan")])
        _, january_audit = build_news_risk_outputs(
            news=january,
            mapping=MAPPING,
            country_weights=COUNTRY_WEIGHTS,
        )
        with_future = pd.DataFrame(
            [
                infectious_news("2024-01-10", "jan"),
                infectious_news("2024-03-10", "mar"),
            ]
        )
        _, combined_audit = build_news_risk_outputs(
            news=with_future,
            mapping=MAPPING,
            country_weights=COUNTRY_WEIGHTS,
        )

        january_weight = float(january_audit.iloc[0]["recency_weight"])
        combined_weight = float(combined_audit[combined_audit["article_id"] == "jan"].iloc[0]["recency_weight"])
        self.assertAlmostEqual(january_weight, combined_weight)

    def test_unapproved_material_mapping_is_not_scored(self):
        mapping = MAPPING.copy()
        mapping["review_status"] = "needs_review"

        scores, audit = build_news_risk_outputs(
            news=pd.DataFrame([infectious_news("2024-01-10", "a1")]),
            mapping=mapping,
            country_weights=COUNTRY_WEIGHTS,
        )

        self.assertTrue(scores.empty)
        self.assertTrue(audit.empty)


class NewsCollectorTest(unittest.TestCase):
    def test_disabled_provider_returns_no_news_signal(self):
        result = collect_news(provider="disabled")

        self.assertTrue(result.empty)
        self.assertEqual(
            list(result.columns),
            ["date", "title", "summary", "source", "country", "url"],
        )

    def test_csv_provider_normalizes_and_deduplicates_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.csv"
            pd.DataFrame(
                [
                    {"date": "2024-01-01", "title": "공급난", "source": "test", "url": "test://1"},
                    {"date": "2024-01-01", "title": "공급난 복제", "source": "test", "url": "test://1"},
                    {"date": "invalid", "title": "잘못된 날짜"},
                ]
            ).to_csv(path, index=False)

            news = load_news_csv(path)

        self.assertEqual(len(news), 1)
        self.assertEqual(list(news.columns), ["date", "title", "summary", "source", "country", "url"])
        self.assertEqual(news.iloc[0]["date"], "2024-01-01")

    def test_csv_provider_requires_path(self):
        with self.assertRaisesRegex(ValueError, "NEWS_DATA_PATH"):
            collect_news(provider="csv")

    @patch("src.news.news_collector._request_gdelt_articles")
    def test_gdelt_collector_normalizes_articles_and_uses_cache(self, request_articles):
        request_articles.return_value = [
            {
                "url": "https://example.com/news",
                "title": "Medical supplies shortage",
                "seendate": "20240115T120000Z",
                "domain": "example.com",
                "sourcecountry": "United States",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "gdelt.csv"
            news = collect_gdelt_news(
                "2024-01-01",
                "2024-01-31",
                cache_path=cache_path,
                request_delay_seconds=0,
            )
            cached = collect_gdelt_news("2024-01-01", "2024-01-31", cache_path=cache_path)

        self.assertEqual(request_articles.call_count, 3)
        self.assertEqual(len(news), 1)
        self.assertEqual(news.iloc[0]["date"], "2024-01-15")
        pd.testing.assert_frame_equal(news, cached)

    def test_english_supply_news_is_classified(self):
        analysis = analyze_news_row(
            pd.Series(
                {
                    "title": "Medical supplies shortage after export ban",
                    "summary": "Imports face a customs delay.",
                    "country": "Global",
                }
            )
        )
        self.assertEqual(analysis["event_type"], "export_restriction_or_sanction")

    def test_middle_east_naphtha_news_emits_reusable_event_and_material_codes(self):
        analysis = analyze_news_row(
            pd.Series(
                {
                    "title": "중동 나프타 가격 급등으로 석유화학 공급 불안",
                    "summary": "호르무즈 운송 차질 우려가 커졌다.",
                    "country": "Global",
                }
            )
        )

        self.assertIn(
            "MIDEAST_NAPHTHA_PETROCHEM_SHOCK",
            analysis["external_event_codes"],
        )
        self.assertIn("POLYPROPYLENE_PP", analysis["material_meta_codes"])
        self.assertIn("POLYETHYLENE_PE", analysis["material_meta_codes"])
        self.assertIn("POLYVINYL_CHLORIDE_PVC", analysis["material_meta_codes"])

    def test_infectious_news_emits_demand_trigger_code(self):
        analysis = analyze_news_row(
            pd.Series(
                {
                    "title": "독감 유행으로 호흡기 환자 증가",
                    "summary": "감염병 확산이 지속되고 있다.",
                    "country": "Korea",
                }
            )
        )

        self.assertIn(
            "RESPIRATORY_INFECTIOUS_DISEASE",
            analysis["demand_risk_meta_codes"],
        )


if __name__ == "__main__":
    unittest.main()
