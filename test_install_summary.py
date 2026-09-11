"""install_summary.py 的单元测试：目标日期、环境变量校验、平台结果封装（不打网络）。"""
import contextlib
import io
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest import mock

import install_summary

ANDROID_ENV = {
    "PLAY_PACKAGE_NAME": "com.example.app",
    "PLAY_SERVICE_ACCOUNT_JSON": '{"type": "service_account"}',
    "PLAY_REPORTS_BUCKET": "gs://pubsite_prod_rev_123/",
}
IOS_ENV = {
    "APPSTORE_APP_ID": "123456789",
    "APPSTORE_KEY_ID": "KEY123",
    "APPSTORE_ISSUER_ID": "ISSUER456",
    "APPSTORE_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
    "APPSTORE_VENDOR_NUMBER": "80012345",
}
TARGET = date(2026, 9, 8)


class TargetDateTest(unittest.TestCase):
    def test_target_is_two_days_before_now(self):
        now = datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(install_summary.target_date(now), date(2026, 9, 8))


class NormalizeBucketTest(unittest.TestCase):
    def test_strips_gs_prefix_and_trailing_slash(self):
        self.assertEqual(
            install_summary.normalize_bucket("gs://pubsite_prod_rev_123/"), "pubsite_prod_rev_123"
        )

    def test_drops_report_path_copied_from_play_console(self):
        self.assertEqual(
            install_summary.normalize_bucket("gs://pubsite_prod_rev_123/stats/installs/"),
            "pubsite_prod_rev_123",
        )

    def test_keeps_plain_bucket_name(self):
        self.assertEqual(install_summary.normalize_bucket("pubsite_prod_rev_123"), "pubsite_prod_rev_123")


class FetchAndroidTest(unittest.TestCase):
    def test_missing_env_reports_error_without_calling_network(self):
        with mock.patch.dict(os.environ, {"PLAY_PACKAGE_NAME": "com.example.app"}, clear=True):
            platform = install_summary.fetch_android(TARGET)
        self.assertIsNone(platform["metrics"])
        self.assertIn("PLAY_REPORTS_BUCKET", platform["error"])

    @mock.patch("install_summary.google_play.fetch_store_performance")
    @mock.patch("install_summary.google_play.make_storage_downloader")
    def test_maps_store_performance_into_metrics_with_conversion_rate(self, mock_downloader, mock_fetch):
        mock_fetch.return_value = {"date": TARGET, "acquisitions": 98, "visitors": 228}
        with mock.patch.dict(os.environ, ANDROID_ENV, clear=True):
            platform = install_summary.fetch_android(TARGET)

        self.assertIsNone(platform["error"])
        self.assertEqual(platform["data_date"], TARGET)
        self.assertEqual(
            platform["metrics"],
            [("详情页访客", 228), ("商店获取用户", 98), ("转化率", "43.0%")],
        )
        mock_downloader.assert_called_once_with("pubsite_prod_rev_123", {"type": "service_account"})
        mock_fetch.assert_called_once_with("com.example.app", TARGET, mock_downloader.return_value)

    @mock.patch("install_summary.google_play.fetch_store_performance")
    @mock.patch("install_summary.google_play.make_storage_downloader")
    def test_zero_visitors_shows_dash_instead_of_dividing_by_zero(self, _downloader, mock_fetch):
        mock_fetch.return_value = {"date": TARGET, "acquisitions": 0, "visitors": 0}
        with mock.patch.dict(os.environ, ANDROID_ENV, clear=True):
            platform = install_summary.fetch_android(TARGET)
        self.assertEqual(platform["metrics"][2], ("转化率", "-"))

    @mock.patch("install_summary.google_play.fetch_store_performance", return_value=None)
    @mock.patch("install_summary.google_play.make_storage_downloader")
    def test_no_data_leaves_metrics_empty_without_error(self, _downloader, _fetch):
        with mock.patch.dict(os.environ, ANDROID_ENV, clear=True):
            platform = install_summary.fetch_android(TARGET)
        self.assertIsNone(platform["metrics"])
        self.assertIsNone(platform["error"])

    @mock.patch("install_summary.google_play.make_storage_downloader", side_effect=RuntimeError("403 Forbidden"))
    def test_exception_is_captured_as_error(self, _downloader):
        with mock.patch.dict(os.environ, ANDROID_ENV, clear=True):
            platform = install_summary.fetch_android(TARGET)
        self.assertEqual(platform["error"], "403 Forbidden")
        self.assertIsNone(platform["metrics"])


class FetchIosTest(unittest.TestCase):
    def test_missing_vendor_number_reports_error(self):
        env = {k: v for k, v in IOS_ENV.items() if k != "APPSTORE_VENDOR_NUMBER"}
        with mock.patch.dict(os.environ, env, clear=True):
            platform = install_summary.fetch_ios(TARGET)
        self.assertIsNone(platform["metrics"])
        self.assertIn("APPSTORE_VENDOR_NUMBER", platform["error"])

    @mock.patch("install_summary.app_store.fetch_daily_sales_rows")
    def test_maps_sales_rows_into_metrics(self, mock_fetch):
        mock_fetch.return_value = [
            {"Apple Identifier": "123456789", "Product Type Identifier": "1F", "Units": "45"},
            {"Apple Identifier": "123456789", "Product Type Identifier": "3F", "Units": "12"},
            {"Apple Identifier": "123456789", "Product Type Identifier": "7F", "Units": "300"},
        ]
        with mock.patch.dict(os.environ, IOS_ENV, clear=True):
            platform = install_summary.fetch_ios(TARGET)

        self.assertIsNone(platform["error"])
        self.assertEqual(platform["data_date"], TARGET)
        self.assertEqual(platform["metrics"], [("首次下载", 45), ("重新下载", 12), ("更新", 300)])
        mock_fetch.assert_called_once_with(
            "80012345", TARGET, "KEY123", "ISSUER456", IOS_ENV["APPSTORE_PRIVATE_KEY"]
        )

    @mock.patch("install_summary.app_store.fetch_daily_sales_rows", side_effect=RuntimeError("HTTP 404：Report is not available yet."))
    def test_exception_is_captured_as_error(self, _fetch):
        with mock.patch.dict(os.environ, IOS_ENV, clear=True):
            platform = install_summary.fetch_ios(TARGET)
        self.assertIn("not available yet", platform["error"])


class MainTest(unittest.TestCase):
    @mock.patch("install_summary.notify.send_to_telegram")
    @mock.patch("install_summary.fetch_ios")
    @mock.patch("install_summary.fetch_android")
    def test_writes_markdown_and_sends_telegram(self, mock_android, mock_ios, mock_send):
        mock_android.return_value = {
            "icon": "📱", "name": "Android", "identifier": "com.example.app",
            "metrics": [("用户安装", 1)], "data_date": TARGET, "error": None,
        }
        mock_ios.return_value = {
            "icon": "🍎", "name": "iOS", "identifier": "123456789",
            "metrics": None, "data_date": None, "error": "boom",
        }
        env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}
        with tempfile.TemporaryDirectory() as tmp:
            report_file = os.path.join(tmp, "install_report.md")
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch("install_summary.REPORT_FILE", report_file), \
                    contextlib.redirect_stdout(io.StringIO()):
                install_summary.main()
            with open(report_file, encoding="utf-8") as f:
                markdown = f.read()

        self.assertIn("# 应用每日安装量", markdown)
        self.assertIn("获取失败：boom", markdown)
        mock_send.assert_called_once()
        bot_token, chat_id, text = mock_send.call_args.args
        self.assertEqual((bot_token, chat_id), ("t", "c"))
        self.assertIn("📈 <b>应用每日安装量</b>", text)

    def test_exits_when_telegram_env_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                install_summary.main()


if __name__ == "__main__":
    unittest.main()
