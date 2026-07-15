"""play_review_summary 纯逻辑部分的单元测试（不依赖 Google API / 网络）。"""
import unittest
from datetime import datetime, timezone

from play_review_summary import (
    build_markdown_report,
    build_telegram_report,
    chunk_message,
    extract_entries,
    summarize,
)


def make_review(seconds, star, text, author="张三", version="1.2.3"):
    """构造一条 Google Play API reviews.list 返回格式的评论。"""
    return {
        "reviewId": f"review-{seconds}-{star}",
        "authorName": author,
        "comments": [
            {
                "userComment": {
                    "text": text,
                    "lastModified": {"seconds": str(seconds), "nanos": 0},
                    "starRating": star,
                    "appVersionName": version,
                }
            }
        ],
    }


SINCE = datetime(2026, 7, 8, tzinfo=timezone.utc)
IN_WINDOW = int(datetime(2026, 7, 10, tzinfo=timezone.utc).timestamp())
OUT_OF_WINDOW = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())


class ExtractEntriesTest(unittest.TestCase):
    def test_keeps_reviews_within_window(self):
        reviews = [make_review(IN_WINDOW, 5, "很好用")]
        entries = extract_entries(reviews, since=SINCE)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["rating"], 5)
        self.assertEqual(entry["text"], "很好用")
        self.assertEqual(entry["author"], "张三")
        self.assertEqual(entry["app_version"], "1.2.3")
        self.assertEqual(
            entry["modified_at"],
            datetime(2026, 7, 10, tzinfo=timezone.utc),
        )

    def test_drops_reviews_before_window(self):
        reviews = [make_review(OUT_OF_WINDOW, 5, "老评论")]
        self.assertEqual(extract_entries(reviews, since=SINCE), [])

    def test_drops_reviews_without_user_comment(self):
        review = {
            "reviewId": "only-dev-reply",
            "authorName": "李四",
            "comments": [{"developerComment": {"text": "感谢反馈"}}],
        }
        self.assertEqual(extract_entries([review], since=SINCE), [])

    def test_sorted_newest_first(self):
        older = make_review(IN_WINDOW, 3, "较旧")
        newer = make_review(IN_WINDOW + 3600, 5, "较新")
        entries = extract_entries([older, newer], since=SINCE)
        self.assertEqual([e["text"] for e in entries], ["较新", "较旧"])


class SummarizeTest(unittest.TestCase):
    def test_four_star_counts_as_good_three_star_as_bad(self):
        entries = extract_entries(
            [
                make_review(IN_WINDOW, 4, "还行"),
                make_review(IN_WINDOW, 3, "一般般"),
            ],
            since=SINCE,
        )
        summary = summarize(entries)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["good"], 1)
        self.assertEqual(summary["bad"], 1)
        self.assertEqual(summary["average"], 3.5)
        self.assertEqual([e["text"] for e in summary["bad_entries"]], ["一般般"])

    def test_empty_entries(self):
        summary = summarize([])
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["good"], 0)
        self.assertEqual(summary["bad"], 0)
        self.assertEqual(summary["average"], 0.0)
        self.assertEqual(summary["bad_entries"], [])


class BuildTelegramReportTest(unittest.TestCase):
    def setUp(self):
        entries = extract_entries(
            [
                make_review(IN_WINDOW, 5, "非常好"),
                make_review(IN_WINDOW, 2, "闪退 <b>严重</b>"),
            ],
            since=SINCE,
        )
        self.report = build_telegram_report(
            package_name="com.example.app",
            summary=summarize(entries),
            period_start=datetime(2026, 7, 8, tzinfo=timezone.utc),
            period_end=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

    def test_contains_counts_and_period(self):
        self.assertIn("com.example.app", self.report)
        self.assertIn("2026-07-08", self.report)
        self.assertIn("2026-07-15", self.report)
        self.assertIn("好评（≥4星）：1", self.report)
        self.assertIn("差评（&lt;4星）：1", self.report)

    def test_lists_bad_reviews_only(self):
        self.assertIn("闪退", self.report)
        self.assertNotIn("非常好", self.report)

    def test_escapes_html_in_user_text(self):
        self.assertNotIn("<b>严重</b>", self.report)
        self.assertIn("&lt;b&gt;严重&lt;/b&gt;", self.report)

    def test_no_bad_reviews_message(self):
        entries = extract_entries([make_review(IN_WINDOW, 5, "好")], since=SINCE)
        report = build_telegram_report(
            package_name="com.example.app",
            summary=summarize(entries),
            period_start=SINCE,
            period_end=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        self.assertIn("本周没有差评", report)


class BuildMarkdownReportTest(unittest.TestCase):
    def test_contains_counts_and_bad_review_table(self):
        entries = extract_entries(
            [
                make_review(IN_WINDOW, 5, "非常好"),
                make_review(IN_WINDOW, 1, "广告太多"),
            ],
            since=SINCE,
        )
        report = build_markdown_report(
            package_name="com.example.app",
            summary=summarize(entries),
            period_start=SINCE,
            period_end=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        self.assertIn("com.example.app", report)
        self.assertIn("| 好评（≥4星） | 1 |", report)
        self.assertIn("| 差评（<4星） | 1 |", report)
        self.assertIn("广告太多", report)
        self.assertNotIn("非常好", report)


class ChunkMessageTest(unittest.TestCase):
    def test_short_message_single_chunk(self):
        self.assertEqual(chunk_message("hello", limit=100), ["hello"])

    def test_splits_at_line_boundaries(self):
        text = "\n".join(["line-%d" % i for i in range(10)])
        chunks = chunk_message(text, limit=30)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 30)
        self.assertEqual("\n".join(chunks).split("\n"), text.split("\n"))

    def test_hard_splits_single_long_line(self):
        text = "x" * 250
        chunks = chunk_message(text, limit=100)
        self.assertEqual(len(chunks), 3)
        self.assertEqual("".join(chunks), text)


if __name__ == "__main__":
    unittest.main()
