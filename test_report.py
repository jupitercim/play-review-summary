"""report.py 的单元测试：统计逻辑 + 合并报告生成（不依赖网络）。"""
import unittest
from datetime import datetime, timezone

import report


def make_entry(rating, text, extra_key, extra_value, author="张三", hour=12):
    return {
        "author": author,
        "rating": rating,
        "text": text,
        extra_key: extra_value,
        "modified_at": datetime(2026, 7, 10, hour, 0, tzinfo=timezone.utc),
    }


def make_platform(icon, name, identifier, extra_key, extra_label, entries=None, error=None):
    if error is not None:
        return {
            "icon": icon,
            "name": name,
            "identifier": identifier,
            "extra_key": extra_key,
            "extra_label": extra_label,
            "summary": None,
            "error": error,
        }
    return {
        "icon": icon,
        "name": name,
        "identifier": identifier,
        "extra_key": extra_key,
        "extra_label": extra_label,
        "summary": report.summarize(entries or []),
        "error": None,
    }


def _section_for(result, icon):
    """从合并报告文本里，按 icon 位置切出对应平台自己的段落文本。"""
    start = result.index(icon)
    other_icons = [i for i in ("📱", "🍎") if i != icon]
    end = len(result)
    for other in other_icons:
        idx = result.find(other, start + 1)
        if idx != -1:
            end = min(end, idx)
    return result[start:end]


PERIOD_START = datetime(2026, 7, 8, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 7, 15, tzinfo=timezone.utc)


class SummarizeTest(unittest.TestCase):
    def test_four_star_counts_as_good_three_star_as_bad(self):
        entries = [
            make_entry(4, "还行", "app_version", "1.2.3"),
            make_entry(3, "一般般", "app_version", "1.2.3"),
        ]
        summary = report.summarize(entries)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["good"], 1)
        self.assertEqual(summary["bad"], 1)
        self.assertEqual(summary["average"], 3.5)
        self.assertEqual([e["text"] for e in summary["bad_entries"]], ["一般般"])

    def test_empty_entries(self):
        summary = report.summarize([])
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["good"], 0)
        self.assertEqual(summary["bad"], 0)
        self.assertEqual(summary["average"], 0.0)
        self.assertEqual(summary["bad_entries"], [])


class BuildTelegramReportTest(unittest.TestCase):
    def test_shows_both_platforms_with_counts(self):
        android = make_platform(
            "📱", "Android", "com.example.app", "app_version", "版本",
            entries=[
                make_entry(5, "非常好", "app_version", "1.2.3"),
                make_entry(2, "闪退 <b>严重</b>", "app_version", "1.2.3"),
            ],
        )
        ios = make_platform(
            "🍎", "iOS", "123456789", "territory", "地区",
            entries=[make_entry(5, "Great app", "territory", "USA")],
        )
        result = report.build_telegram_report(android, ios, PERIOD_START, PERIOD_END)
        self.assertIn("Android", result)
        self.assertIn("iOS", result)
        self.assertIn("com.example.app", result)
        self.assertIn("123456789", result)
        self.assertIn("好评（≥4星）：1", result)
        self.assertIn("差评（&lt;4星）：1", result)
        self.assertIn("2026-07-08", result)
        self.assertIn("2026-07-15", result)

    def test_android_bad_review_shows_version_ios_shows_territory(self):
        android = make_platform(
            "📱", "Android", "com.example.app", "app_version", "版本",
            entries=[make_entry(1, "广告太多", "app_version", "9.9.9")],
        )
        ios = make_platform(
            "🍎", "iOS", "123456789", "territory", "地区",
            entries=[make_entry(1, "Too many ads", "territory", "CHN")],
        )
        result = report.build_telegram_report(android, ios, PERIOD_START, PERIOD_END)
        self.assertIn("版本：9.9.9", result)
        self.assertIn("地区：CHN", result)

    def test_escapes_html_in_user_text(self):
        android = make_platform(
            "📱", "Android", "com.example.app", "app_version", "版本",
            entries=[make_entry(2, "闪退 <b>严重</b>", "app_version", "1.2.3")],
        )
        ios = make_platform("🍎", "iOS", "123456789", "territory", "地区", entries=[])
        result = report.build_telegram_report(android, ios, PERIOD_START, PERIOD_END)
        self.assertNotIn("<b>严重</b>", result)
        self.assertIn("&lt;b&gt;严重&lt;/b&gt;", result)

    def test_no_bad_reviews_message(self):
        android = make_platform(
            "📱", "Android", "com.example.app", "app_version", "版本",
            entries=[make_entry(5, "好", "app_version", "1.2.3")],
        )
        ios = make_platform(
            "🍎", "iOS", "123456789", "territory", "地区",
            entries=[make_entry(5, "Good", "territory", "USA")],
        )
        result = report.build_telegram_report(android, ios, PERIOD_START, PERIOD_END)
        self.assertEqual(result.count("本周没有差评"), 2)

    def test_platform_error_shows_failure_notice_and_skips_stats(self):
        android = make_platform(
            "📱", "Android", "（未配置）", "app_version", "版本",
            error="缺少 PLAY_PACKAGE_NAME / PLAY_SERVICE_ACCOUNT_JSON 环境变量",
        )
        ios = make_platform(
            "🍎", "iOS", "123456789", "territory", "地区",
            entries=[make_entry(5, "Good", "territory", "USA")],
        )
        result = report.build_telegram_report(android, ios, PERIOD_START, PERIOD_END)
        android_section = _section_for(result, "📱")
        self.assertIn("获取失败", android_section)
        self.assertIn("缺少 PLAY_PACKAGE_NAME", android_section)
        self.assertNotIn("评论总数", android_section)

    def test_both_platforms_failed_still_produces_message(self):
        android = make_platform("📱", "Android", "（未配置）", "app_version", "版本", error="网络超时")
        ios = make_platform("🍎", "iOS", "（未配置）", "territory", "地区", error="401 Unauthorized")
        result = report.build_telegram_report(android, ios, PERIOD_START, PERIOD_END)
        self.assertEqual(result.count("获取失败"), 2)
        self.assertIn("网络超时", result)
        self.assertIn("401 Unauthorized", result)

    def test_no_reviews_shows_single_line_message(self):
        android = make_platform("📱", "Android", "com.example.app", "app_version", "版本", entries=[])
        ios = make_platform("🍎", "iOS", "123456789", "territory", "地区", entries=[])
        result = report.build_telegram_report(android, ios, PERIOD_START, PERIOD_END)
        self.assertEqual(result.count("本周无评论"), 2)
        self.assertNotIn("评论总数", result)
        self.assertNotIn("本周没有差评", result)


class BuildMarkdownReportTest(unittest.TestCase):
    def test_contains_counts_and_bad_review_table_for_both_platforms(self):
        android = make_platform(
            "📱", "Android", "com.example.app", "app_version", "版本",
            entries=[
                make_entry(5, "非常好", "app_version", "1.2.3"),
                make_entry(1, "广告太多", "app_version", "1.2.3"),
            ],
        )
        ios = make_platform(
            "🍎", "iOS", "123456789", "territory", "地区",
            entries=[make_entry(1, "Too many ads", "territory", "CHN")],
        )
        result = report.build_markdown_report(android, ios, PERIOD_START, PERIOD_END)
        self.assertIn("com.example.app", result)
        self.assertIn("123456789", result)
        self.assertIn("| 好评（≥4星） | 1 |", result)
        self.assertIn("广告太多", result)
        self.assertIn("Too many ads", result)
        self.assertNotIn("非常好", result)

    def test_markdown_shows_failure_notice(self):
        android = make_platform(
            "📱", "Android", "（未配置）", "app_version", "版本",
            error="网络超时",
        )
        ios = make_platform(
            "🍎", "iOS", "123456789", "territory", "地区",
            entries=[make_entry(5, "Good", "territory", "USA")],
        )
        result = report.build_markdown_report(android, ios, PERIOD_START, PERIOD_END)
        android_section = _section_for(result, "📱")
        self.assertIn("获取失败", android_section)
        self.assertIn("网络超时", android_section)
        self.assertNotIn("评论总数", android_section)

    def test_no_reviews_shows_single_line_message(self):
        android = make_platform("📱", "Android", "com.example.app", "app_version", "版本", entries=[])
        ios = make_platform("🍎", "iOS", "123456789", "territory", "地区", entries=[])
        result = report.build_markdown_report(android, ios, PERIOD_START, PERIOD_END)
        self.assertEqual(result.count("本周无评论"), 2)
        self.assertNotIn("评论总数", result)
        self.assertNotIn("没有差评", result)


class ChunkMessageTest(unittest.TestCase):
    def test_short_message_single_chunk(self):
        self.assertEqual(report.chunk_message("hello", limit=100), ["hello"])

    def test_splits_at_line_boundaries(self):
        text = "\n".join(["line-%d" % i for i in range(10)])
        chunks = report.chunk_message(text, limit=30)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 30)
        self.assertEqual("\n".join(chunks).split("\n"), text.split("\n"))

    def test_hard_splits_single_long_line(self):
        text = "x" * 250
        chunks = report.chunk_message(text, limit=100)
        self.assertEqual(len(chunks), 3)
        self.assertEqual("".join(chunks), text)


if __name__ == "__main__":
    unittest.main()
