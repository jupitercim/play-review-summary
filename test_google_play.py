"""google_play.py 纯逻辑部分的单元测试（不依赖 Google API / 网络）。"""
import unittest
from datetime import datetime, timezone

from google_play import extract_entries


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


if __name__ == "__main__":
    unittest.main()
