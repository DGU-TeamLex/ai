from datetime import datetime, timezone
import gzip
import math
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import pandas as pd

from src.news.news_risk_scorer import build_news_risk_outputs
from src.news.news_collector import (
    _find_recent_gdelt_ngram_batches,
    _news_from_gdelt_ngram_batch,
    _request_gdelt_articles,
    collect_gdelt_news,
    collect_news,
    load_news_csv,
)
from src.news.news_llm_analyzer import analyze_news_row


MAPPING = pd.DataFrame(
    [
        {
            "stock_item_key": "INST::DEPT::RESP1",
            "item_code": "RESP1",
            "item_name": "호흡기 물품",
            "related_material": "respiratory disease",
            "demand_risk_meta_code": "INFECTIOUS_DISEASE_OUTBREAK",
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

    def test_duplicate_mapping_rows_score_an_article_once_per_stock_item(self):
        duplicated_mapping = pd.concat([MAPPING.iloc[[0]], MAPPING.iloc[[0]]])

        scores, audit = build_news_risk_outputs(
            news=pd.DataFrame([infectious_news("2024-01-10", "a1")]),
            mapping=duplicated_mapping,
            country_weights=COUNTRY_WEIGHTS,
        )

        self.assertEqual(len(audit), 1)
        self.assertEqual(len(scores), 1)

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
    def test_gdelt_ngram_parser_requires_topic_and_risk_context(self):
        with tempfile.TemporaryDirectory() as directory:
            ngram_path = Path(directory) / "batch.ngrams.txt.gz"
            toc_path = Path(directory) / "batch.toc.json.gz"
            with gzip.open(ngram_path, "wt", encoding="utf-8") as ngrams:
                ngrams.write("1\tmedical device production faces\t1\n")
                ngrams.write("1\tsupply disruption after shutdown\t1\n")
                ngrams.write("2\tnew cotton summer shirts\t1\n")
                ngrams.write("3\trising cases of influenza\t1\n")
                ngrams.write("4\tfollowing the COVID outbreak\t1\n")
                ngrams.write("5\tnew HIV infections reported\t1\n")
            with gzip.open(toc_path, "wt", encoding="utf-8") as toc:
                toc.write(
                    '{"ID":1,"date":"2026-07-28T09:31:00.000Z",'
                    '"lang":"en","title":"Medical device disruption",'
                    '"url":"https://example.com/device"}\n'
                )
                toc.write(
                    '{"ID":2,"date":"2026-07-28T09:31:00.000Z",'
                    '"lang":"en","title":"Cotton clothing sale",'
                    '"url":"https://example.com/clothing"}\n'
                )
                toc.write(
                    '{"ID":3,"date":"2026-07-28T09:31:00.000Z",'
                    '"lang":"en","title":"Influenza cases rise",'
                    '"url":"https://example.com/influenza"}\n'
                )
                toc.write(
                    '{"ID":4,"date":"2026-07-28T09:31:00.000Z",'
                    '"lang":"en","title":"Old concert after COVID",'
                    '"url":"https://example.com/2020/old-concert"}\n'
                )
                toc.write(
                    '{"ID":5,"date":"2026-07-28T09:31:00.000Z",'
                    '"lang":"en","title":"HIV law reform approved",'
                    '"url":"https://example.com/hiv-law"}\n'
                )

            news = _news_from_gdelt_ngram_batch(
                ngram_path,
                toc_path,
                languages={"en"},
            )

        self.assertEqual(
            {row["url"] for row in news},
            {
                "https://example.com/device",
                "https://example.com/influenza",
            },
        )
        self.assertTrue(all("ngram_topics=" in row["summary"] for row in news))

    @patch("src.news.news_collector._remote_file_exists")
    def test_gdelt_ngram_batch_discovery_skips_missing_minutes(self, exists):
        exists.side_effect = [False, True, False, True]

        batches = _find_recent_gdelt_ngram_batches(
            batch_count=2,
            lookback_minutes=4,
            now=datetime(2026, 7, 28, 9, 47, tzinfo=timezone.utc),
        )

        self.assertEqual(batches, ["20260728093900", "20260728094100"])

    @patch("src.news.news_collector.time.sleep")
    @patch("src.news.news_collector.urlopen")
    def test_gdelt_request_retries_rate_limit_with_configured_backoff(
        self,
        urlopen,
        sleep,
    ):
        rate_limit = HTTPError(
            "https://api.gdeltproject.org",
            429,
            "Too Many Requests",
            {},
            None,
        )
        success = io.BytesIO(
            b'{"articles": [{"title": "Medical supply shortage"}]}'
        )
        urlopen.side_effect = [rate_limit, success]

        with patch.dict(
            os.environ,
            {
                "GDELT_MAX_RETRIES": "2",
                "GDELT_RATE_LIMIT_BACKOFF_SECONDS": "7",
                "GDELT_MAX_BACKOFF_SECONDS": "20",
            },
        ):
            articles = _request_gdelt_articles(
                "medical supplies",
                pd.Timestamp("2024-01-01"),
                pd.Timestamp("2024-01-31"),
                250,
            )

        self.assertEqual(articles[0]["title"], "Medical supply shortage")
        sleep.assert_called_once_with(7.0)

    @patch("src.news.news_collector.time.sleep")
    @patch("src.news.news_collector.urlopen")
    def test_gdelt_request_retries_non_json_response(self, urlopen, sleep):
        urlopen.side_effect = [
            io.BytesIO(b"temporarily unavailable"),
            io.BytesIO(b'{"articles": []}'),
        ]

        with patch.dict(
            os.environ,
            {
                "GDELT_MAX_RETRIES": "1",
                "GDELT_RATE_LIMIT_BACKOFF_SECONDS": "3",
                "GDELT_MAX_BACKOFF_SECONDS": "10",
            },
        ):
            articles = _request_gdelt_articles(
                "influenza",
                pd.Timestamp("2024-01-01"),
                pd.Timestamp("2024-01-31"),
                10,
            )

        self.assertEqual(articles, [])
        sleep.assert_called_once_with(3.0)

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

    @patch("src.news.news_collector._request_gdelt_articles")
    def test_gdelt_combined_mode_uses_one_request_per_month(self, request_articles):
        request_articles.return_value = []

        news = collect_gdelt_news(
            "2024-01-01",
            "2024-01-31",
            request_delay_seconds=0,
            query_mode="combined",
        )

        self.assertEqual(request_articles.call_count, 1)
        self.assertTrue(news.empty)

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
            "INFECTIOUS_DISEASE_OUTBREAK",
            analysis["demand_risk_meta_codes"],
        )

    def test_unconfirmed_infectious_case_has_no_effect(self):
        analysis = analyze_news_row(
            pd.Series(
                {
                    "title": "No Confirmed Mpox Case as Investigation Begins",
                    "summary": "Officials investigated a suspected outbreak.",
                    "country": "Nigeria",
                }
            )
        )

        self.assertEqual(analysis["event_type"], "none")
        self.assertEqual(analysis["risk_direction"], "no_effect")

    def test_falling_crude_oil_price_is_supply_relief(self):
        analysis = analyze_news_row(
            pd.Series(
                {
                    "title": "Oil Prices Fall as Supply Concerns Ease",
                    "summary": "Brent crude fell after talks resumed.",
                    "country": "Global",
                }
            )
        )

        self.assertEqual(analysis["event_type"], "raw_material_price_relief")
        self.assertEqual(analysis["risk_direction"], "supply_increase")

    def test_supply_relief_article_is_not_scored_as_inventory_risk(self):
        mapping = pd.DataFrame(
            [
                {
                    "stock_item_key": "INST::DEPT::PP1",
                    "item_code": "PP1",
                    "item_name": "주사기",
                    "related_material": "oil_plastic",
                    "raw_material_meta_code": "CRUDE_OIL_REFINED",
                    "mapping_weight": 1.0,
                }
            ]
        )
        news = pd.DataFrame(
            [
                {
                    "date": "2026-07-28",
                    "title": "Oil Prices Fall as Supply Concerns Ease",
                    "summary": "Brent crude fell after talks resumed.",
                    "source": "Reuters",
                    "country": "Global",
                    "url": "test://oil-relief",
                }
            ]
        )

        scores, audit = build_news_risk_outputs(
            news=news,
            mapping=mapping,
            country_weights=COUNTRY_WEIGHTS,
        )

        self.assertTrue(scores.empty)
        self.assertTrue(audit.empty)


if __name__ == "__main__":
    unittest.main()


class NewsFilterCoversClassifierTest(unittest.TestCase):
    """입구 필터가 분류기보다 좁으면 안 된다.

    filter_relevant_news 에서 걸러진 기사는 news_llm_analyzer 가 볼 기회조차
    없다. 실제로 좁아서 수집 7,756건 중 991건(12.8%)만 통과했고, 탈락분에
    port_or_logistics_disruption 으로 잡힐 기사가 대량으로 있었다. 그 결과
    supply_news_risk 가 비영 0% 였다.
    """

    CLASSIFIABLE_TITLES = [
        "Port strike halts container operations at major terminal",
        "US customs computer outage causes delays for airport travelers",
        "Company declares force majeure after plant explosion",
        "New sanctions restrict aluminium exports",
        "Red Sea shipping disruption forces vessels to reroute",
        "Factory shutdown cuts polypropylene output",
        "Freight backlog grows amid port congestion",
    ]

    def test_filter_keeps_everything_the_classifier_can_classify(self):
        from src.news.news_filter import filter_relevant_news
        from src.news.news_llm_analyzer import analyze_news_row

        news = pd.DataFrame(
            [
                {
                    "date": "2024-05-01",
                    "title": title,
                    "summary": "",
                    "source": "reuters.com",
                    "country": "Unknown",
                    "url": f"https://example.invalid/{index}",
                }
                for index, title in enumerate(self.CLASSIFIABLE_TITLES)
            ]
        )
        classified = [
            title
            for title, (_, row) in zip(self.CLASSIFIABLE_TITLES, news.iterrows())
            if analyze_news_row(row)["event_type"] != "none"
        ]
        self.assertTrue(classified, "분류기가 하나도 못 잡으면 이 테스트가 무의미하다")

        kept = set(filter_relevant_news(news)["title"])
        dropped = [title for title in classified if title not in kept]
        self.assertEqual(
            dropped,
            [],
            f"분류 가능한 기사가 입구 필터에서 탈락한다: {dropped}",
        )
