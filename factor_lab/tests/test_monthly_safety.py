"""Targeted local checks for monthly catch-up and stale-data gates (no network)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "data"
sys.path.insert(0, str(DATA))
import download_cb  # noqa: E402
import monthly_review  # noqa: E402


class MonthlySafetyTests(unittest.TestCase):
    def test_calendar_fails_closed_and_skips_closed_days(self):
        with tempfile.TemporaryDirectory() as temp:
            calendar = Path(temp) / "trade_cal.parquet"
            with patch.object(download_cb, "CAL_PATH", calendar):
                with self.assertRaisesRegex(RuntimeError, "缺少交易日历"):
                    download_cb.trading_days("20260901", "20260903")
                pd.DataFrame({"cal_date": ["20260901", "20260902", "20260903"],
                              "is_open": [1, 0, 1]}).to_parquet(calendar)
                self.assertEqual(download_cb.trading_days("20260901", "20260903"),
                                 ["20260901", "20260903"])
                with self.assertRaisesRegex(RuntimeError, "未覆盖"):
                    download_cb.trading_days("20260901", "20260904")

    def test_incremental_repairs_hole_below_cached_max(self):
        with tempfile.TemporaryDirectory() as temp:
            daily = Path(temp) / "cb_daily.parquet"
            calendar = Path(temp) / "trade_cal.parquet"
            pd.DataFrame({"ts_code": ["A", "A"], "trade_date": ["20260901", "20260903"],
                          "close": [100, 102]}).to_parquet(daily)
            pd.DataFrame({"cal_date": ["20260901", "20260902", "20260903"],
                          "is_open": [1, 1, 1]}).to_parquet(calendar)
            called = []
            def fetch(_pro, days):
                called.extend(days)
                return [pd.DataFrame({"ts_code": ["A"], "trade_date": ["20260902"],
                                      "close": [101]})], []
            with patch.object(download_cb, "DAILY_PATH", daily), \
                 patch.object(download_cb, "CAL_PATH", calendar), \
                 patch.object(download_cb, "get_pro", return_value=object()), \
                 patch.object(download_cb, "fetch_by_day", side_effect=fetch), \
                 patch.object(download_cb, "load_or_fetch_basic", return_value=pd.DataFrame()), \
                 patch.object(download_cb, "refresh_redeem", return_value=True), \
                 patch.object(download_cb, "check_convert"):
                result = download_cb.run_incremental(
                    SimpleNamespace(start=None, end="20260903", skip_redeem=False))
            self.assertEqual(result, 0)
            self.assertEqual(called, ["20260902"])
            self.assertEqual(set(pd.read_parquet(daily)["trade_date"]),
                             {"20260901", "20260902", "20260903"})

    def test_review_existing_date_finds_previous_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for date, code in (("2026-08-31", "110001"), ("2026-09-30", "110002")):
                pd.DataFrame({"code": [code], "target_weight": [0.02],
                              "target_amount": [2000], "name": ["CB"],
                              "double_low_value": [100], "price": [100],
                              "premium_rate": [0]}).to_csv(folder / f"{date}.csv", index=False)
            tracking = folder / "TRACKING.md"
            tracking.write_text("# Tracking\n", encoding="utf-8")
            args = ["monthly_review.py", "--signal-dir", str(folder),
                    "--tracking-file", str(tracking), "--date", "2026-09-30"]
            with patch.object(sys, "argv", args):
                self.assertEqual(monthly_review.main(), 0)
                self.assertEqual(monthly_review.main(), 0)
            text = tracking.read_text(encoding="utf-8")
            self.assertIn("新买入 1 只、卖出 1 只", text)
            self.assertEqual(text.count("### 复盘 2026-09-30"), 1)


if __name__ == "__main__":
    unittest.main()