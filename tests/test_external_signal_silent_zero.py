import logging
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from src.config import load_dotenv
from src.feature_engineering import _merge_risk
from src.trade.trade_collector import collect_trade_flows


class DotenvLoadingTest(unittest.TestCase):
    """운영 가이드 21.1 대로 .env 만 만들어도 설정이 실제로 적용돼야 한다."""

    def test_env_file_values_are_loaded(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# comment\n"
                "TEAMLEX_TEST_PROVIDER=kcs\n"
                "TEAMLEX_TEST_QUOTED=\"quoted value\"\n"
                "\n",
                encoding="utf-8",
            )
            for key in ("TEAMLEX_TEST_PROVIDER", "TEAMLEX_TEST_QUOTED"):
                os.environ.pop(key, None)

            loaded = load_dotenv(path)

            self.assertIn("TEAMLEX_TEST_PROVIDER", loaded)
            self.assertEqual(os.environ["TEAMLEX_TEST_PROVIDER"], "kcs")
            self.assertEqual(os.environ["TEAMLEX_TEST_QUOTED"], "quoted value")
            for key in ("TEAMLEX_TEST_PROVIDER", "TEAMLEX_TEST_QUOTED"):
                os.environ.pop(key, None)

    def test_existing_environment_wins(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("TEAMLEX_TEST_EXISTING=from_file\n", encoding="utf-8")
            os.environ["TEAMLEX_TEST_EXISTING"] = "from_shell"

            load_dotenv(path)

            self.assertEqual(os.environ["TEAMLEX_TEST_EXISTING"], "from_shell")
            os.environ.pop("TEAMLEX_TEST_EXISTING", None)

    def test_missing_file_is_not_an_error(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(load_dotenv(Path(tmp) / "absent.env"), [])


class MergeRiskWarningTest(unittest.TestCase):
    """외부신호가 0 으로 채워질 때는 반드시 이유가 로그에 남아야 한다."""

    def _feature_table(self):
        return pd.DataFrame(
            {
                "year_month": pd.to_datetime(["2025-01-01", "2025-02-01"]),
                "stock_item_key": ["A::B::C", "A::B::D"],
            }
        )

    def test_missing_risk_file_warns_and_zero_fills(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "absent.csv"
            with self.assertLogs("src.feature_engineering", level=logging.WARNING) as logs:
                result = _merge_risk(self._feature_table(), missing, ["commodity_risk"])

        self.assertTrue((result["commodity_risk"] == 0.0).all())
        self.assertIn("commodity_risk", "\n".join(logs.output))

    def test_empty_risk_file_warns_and_zero_fills(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.csv"
            pd.DataFrame(columns=["STD_YYYYMM", "stock_item_key", "commodity_risk"]).to_csv(
                path, index=False
            )
            with self.assertLogs("src.feature_engineering", level=logging.WARNING) as logs:
                result = _merge_risk(self._feature_table(), path, ["commodity_risk"])

        self.assertTrue((result["commodity_risk"] == 0.0).all())
        self.assertIn("empty", "\n".join(logs.output).lower())


class TradeCacheCoverageTest(unittest.TestCase):
    """캐시가 요청한 HS 코드를 다 담고 있을 때만 수집을 건너뛰어야 한다."""

    def _write_cache(self, path: Path, hs_codes: list[str]):
        pd.DataFrame(
            {
                "STD_YYYYMM": ["2025-01"] * len(hs_codes),
                "hs_code": hs_codes,
                "country_code": ["ALL"] * len(hs_codes),
                "country_name": ["전체"] * len(hs_codes),
                "export_weight_kg": [1.0] * len(hs_codes),
                "export_value_usd": [1.0] * len(hs_codes),
                "import_weight_kg": [1.0] * len(hs_codes),
                "import_value_usd": [1.0] * len(hs_codes),
            }
        ).to_csv(path, index=False)

    def test_fully_cached_codes_skip_collection(self):
        with TemporaryDirectory() as tmp:
            total = Path(tmp) / "total.csv"
            country = Path(tmp) / "country.csv"
            self._write_cache(total, ["3902100000"])
            self._write_cache(country, ["3902100000"])

            totals, _ = collect_trade_flows(
                ["3902100000"],
                provider="kcs",
                total_cache_path=total,
                country_cache_path=country,
                refresh=False,
            )

        self.assertFalse(totals.empty)

    def test_newly_requested_code_is_not_silently_skipped(self):
        """캐시에 없는 코드를 요청하면 캐시를 그대로 반환해서는 안 된다."""
        with TemporaryDirectory() as tmp:
            total = Path(tmp) / "total.csv"
            country = Path(tmp) / "country.csv"
            self._write_cache(total, ["3902100000"])
            self._write_cache(country, ["3902100000"])

            # 수집 경로로 내려가면 서비스 키가 없어 실패한다.
            # 조용히 캐시를 반환하던 이전 동작에서는 이 예외가 발생하지 않았다.
            with patch.dict(os.environ, {"DATA_GO_KR_SERVICE_KEY": ""}, clear=False):
                with self.assertRaises(ValueError):
                    collect_trade_flows(
                        ["3902100000", "9018310000"],
                        provider="kcs",
                        total_cache_path=total,
                        country_cache_path=country,
                        refresh=False,
                    )


if __name__ == "__main__":
    unittest.main()
