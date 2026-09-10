"""install_report.py 的单元测试：每日安装量 Telegram / Markdown 报告生成。"""
import unittest
from datetime import date

import install_report

TARGET = date(2026, 9, 8)


def make_platform(icon, name, identifier, metrics=None, data_date=None, error=None):
    return {
        "icon": icon,
        "name": name,
        "identifier": identifier,
        "metrics": metrics,
        "data_date": data_date,
        "error": error,
    }


def android_ok(data_date=TARGET):
    return make_platform(
        "📱", "Android", "com.example.app",
        metrics=[("用户安装", 123), ("设备安装", 130), ("设备卸载", 20)],
        data_date=data_date,
    )


def ios_ok(data_date=TARGET):
    return make_platform(
        "🍎", "iOS", "123456789",
        metrics=[("首次下载", 45), ("重新下载", 12), ("更新", 300)],
        data_date=data_date,
    )


class TelegramReportTest(unittest.TestCase):
    def test_includes_date_and_metrics_for_both_platforms(self):
        text = install_report.build_telegram_report(android_ok(), ios_ok(), TARGET)
        self.assertIn("2026-09-08", text)
        self.assertIn("📱 <b>Android</b>", text)
        self.assertIn("用户安装：123", text)
        self.assertIn("设备卸载：20", text)
        self.assertIn("🍎 <b>iOS</b>", text)
        self.assertIn("首次下载：45", text)
        self.assertIn("更新：300", text)
        self.assertNotIn("尚未生成", text)

    def test_shows_escaped_error_for_failed_platform(self):
        failed = make_platform("🍎", "iOS", "123456789", error="HTTP 403 <forbidden>")
        text = install_report.build_telegram_report(android_ok(), failed, TARGET)
        self.assertIn("⚠️ 获取失败：HTTP 403 &lt;forbidden&gt;", text)
        self.assertNotIn("<forbidden>", text)

    def test_shows_not_ready_when_platform_has_no_data(self):
        empty = make_platform("📱", "Android", "com.example.app")
        text = install_report.build_telegram_report(empty, ios_ok(), TARGET)
        self.assertIn("尚未生成", text)

    def test_flags_fallback_when_data_is_for_an_earlier_day(self):
        text = install_report.build_telegram_report(
            android_ok(data_date=date(2026, 9, 7)), ios_ok(), TARGET
        )
        self.assertIn("2026-09-07", text)
        self.assertIn("尚未生成", text)
        self.assertIn("用户安装：123", text)

    def test_fits_in_a_single_telegram_message(self):
        text = install_report.build_telegram_report(android_ok(), ios_ok(), TARGET)
        self.assertLessEqual(len(text), 4096)


class MarkdownReportTest(unittest.TestCase):
    def test_renders_metric_table_per_platform(self):
        text = install_report.build_markdown_report(android_ok(), ios_ok(), TARGET)
        self.assertIn("# 应用每日安装量", text)
        self.assertIn("2026-09-08", text)
        self.assertIn("## 📱 Android", text)
        self.assertIn("| 用户安装 | 123 |", text)
        self.assertIn("## 🍎 iOS", text)
        self.assertIn("| 首次下载 | 45 |", text)

    def test_markdown_shows_error_and_fallback_notes(self):
        failed = make_platform("🍎", "iOS", "123456789", error="boom")
        text = install_report.build_markdown_report(
            android_ok(data_date=date(2026, 9, 7)), failed, TARGET
        )
        self.assertIn("获取失败：boom", text)
        self.assertIn("2026-09-07", text)
        self.assertIn("尚未生成", text)


if __name__ == "__main__":
    unittest.main()
