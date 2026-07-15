# iOS（App Store Connect）评价接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让现有 Google Play 评价周报工具同时拉取 iOS（App Store Connect API）评论，并把两个平台的统计结果合并进同一条 Telegram 消息、同一份 Markdown 报告。

**Architecture:** 按平台拆分抓取逻辑（`google_play.py` / `app_store.py`），统计与报告生成收拢到一个平台无关的 `report.py`，新入口 `review_summary.py` 负责编排"读环境变量 → 分别拉取（各自容错）→ 统计 → 合并报告 → 推送"。

**Tech Stack:** Python 3.12、`requests`、`google-api-python-client` + `google-auth`（Android，既有）、`PyJWT[crypto]`（iOS JWT 签名，新增）、`unittest`（测试）、GitHub Actions（既有定时任务）。

参考设计文档：`docs/superpowers/specs/2026-07-15-ios-review-integration-design.md`

## Global Constraints

- 好评阈值：rating ≥ 4 为好评，< 4 为差评（两平台一致，不变）。
- 统计窗口：`REVIEW_WINDOW_DAYS = 7`（两平台一致，不变）。
- Telegram 单条消息上限 4096 字符，复用 `chunk_message()`（不变）。
- 只统计带文字内容的评论；纯星级评分两边接口都不提供，无法统计（不变）。
- Android entry 字典字段：`author, rating, text, app_version, modified_at`；iOS entry 字典字段：`author, rating, text, territory, modified_at`。两者字段名不得互相冒充（Android 不伪造 territory，iOS 不伪造 app_version）。
- iOS 拉取按 `-createdDate` 倒序分页；一旦当页最旧一条评论早于 7 天窗口起点，即停止翻页（Apple 接口是全量历史数据，需要主动截断，不能依赖接口自然翻到底）。
- 单平台拉取失败（含该平台专属环境变量缺失）：只在该平台对应的报告段落里展示"⚠️ 获取失败：<错误信息>"，不得让整个脚本 `sys.exit`。`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` 仍然是硬性必需项（缺失时沿用原有 `require_env` 直接退出），因为没有它们整条报告都发不出去。
- 新增依赖：`PyJWT[crypto]>=2.8.0`（ES256 JWT 签名）。
- 所有单元测试通过 `python3 -m unittest discover -v` 统一运行，不依赖真实网络 / 真实密钥。

---

### Task 1: 迁移 Android 抓取逻辑到 `google_play.py`

**Files:**
- Create: `google_play.py`
- Test: `test_google_play.py`

**Interfaces:**
- Produces: `fetch_reviews(package_name: str, service_account_info: dict) -> list[dict]` — 原样返回 Google Play `reviews.list` 的 review 字典列表。
- Produces: `extract_entries(reviews: list[dict], since: datetime) -> list[dict]` — 每个 entry 为 `{"author": str, "rating": int, "text": str, "app_version": str, "modified_at": datetime}`，按 `modified_at` 倒序。

- [ ] **Step 1: 写失败的测试 `test_google_play.py`**

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python3 -m unittest test_google_play -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'google_play'`

- [ ] **Step 3: 实现 `google_play.py`**

```python
"""Android（Google Play）评论拉取与解析。"""
from datetime import datetime, timezone


def fetch_reviews(package_name, service_account_info):
    """分页拉取 Google Play 的全部评论（API 只保留最近约 7 天的数据）。"""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/androidpublisher"],
    )
    service = build(
        "androidpublisher", "v3", credentials=credentials, cache_discovery=False
    )

    reviews = []
    token = None
    while True:
        kwargs = {"packageName": package_name, "maxResults": 100}
        if token:
            kwargs["token"] = token
        response = service.reviews().list(**kwargs).execute()
        reviews.extend(response.get("reviews", []))
        token = response.get("tokenPagination", {}).get("nextPageToken")
        if not token:
            break
    return reviews


def extract_entries(reviews, since):
    """把 API 返回的评论转成扁平结构，只保留 since 之后有更新的，按时间倒序。"""
    entries = []
    for review in reviews:
        user_comment = None
        for comment in review.get("comments", []):
            if "userComment" in comment:
                user_comment = comment["userComment"]
                break
        if not user_comment:
            continue

        seconds = int(user_comment.get("lastModified", {}).get("seconds", 0))
        modified_at = datetime.fromtimestamp(seconds, tz=timezone.utc)
        if modified_at < since:
            continue

        entries.append(
            {
                "author": review.get("authorName") or "匿名用户",
                "rating": int(user_comment.get("starRating", 0)),
                "text": (user_comment.get("text") or "").strip(),
                "app_version": user_comment.get("appVersionName", ""),
                "modified_at": modified_at,
            }
        )
    entries.sort(key=lambda e: e["modified_at"], reverse=True)
    return entries
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python3 -m unittest test_google_play -v`
Expected: PASS（4 个测试全绿）

- [ ] **Step 5: 提交**

```bash
git add google_play.py test_google_play.py
git commit -m "feat: extract Android review fetching into google_play module"
```

---

### Task 2: 平台无关的报告生成 `report.py`

**Files:**
- Create: `report.py`
- Test: `test_report.py`

**Interfaces:**
- Produces: `summarize(entries: list[dict]) -> dict`，返回 `{"total", "good", "bad", "average", "bad_entries"}`。
- Produces: `chunk_message(text: str, limit: int = 4096) -> list[str]`。
- Produces: `build_telegram_report(android: dict, ios: dict, period_start: datetime, period_end: datetime) -> str`。
- Produces: `build_markdown_report(android: dict, ios: dict, period_start: datetime, period_end: datetime) -> str`。
- Consumes（本任务自定义、Task 4 必须遵守的"平台结果字典"契约）：
  ```
  {
      "icon": "📱" 或 "🍎",
      "name": "Android" 或 "iOS",
      "identifier": str,        # package name 或 App Store Connect 数字 App ID
      "extra_key": "app_version" 或 "territory",
      "extra_label": "版本" 或 "地区",
      "summary": summarize() 的返回值，或 None（拉取失败时）,
      "error": str，或 None（成功时）,
  }
  ```

- [ ] **Step 1: 写失败的测试 `test_report.py`**

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python3 -m unittest test_report -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'report'`

- [ ] **Step 3: 实现 `report.py`**

```python
"""平台无关的统计与报告生成：把 Android / iOS 的评论 entries 汇总成 Telegram / Markdown 报告。

平台结果字典（android / ios 参数）的形状：
{
    "icon": "📱" 或 "🍎",
    "name": "Android" 或 "iOS",
    "identifier": 应用标识（package name 或 App Store Connect 数字 App ID）,
    "extra_key": entry 字典里除 author/rating/text/modified_at 外的额外字段名（"app_version" 或 "territory"）,
    "extra_label": 额外字段展示用的中文标签（"版本" 或 "地区"）,
    "summary": summarize() 的返回值，拉取失败时为 None,
    "error": 错误信息字符串，成功时为 None,
}
"""
import html

GOOD_RATING_THRESHOLD = 4
TELEGRAM_MESSAGE_LIMIT = 4096


def summarize(entries):
    good = [e for e in entries if e["rating"] >= GOOD_RATING_THRESHOLD]
    bad = [e for e in entries if e["rating"] < GOOD_RATING_THRESHOLD]
    average = (
        sum(e["rating"] for e in entries) / len(entries) if entries else 0.0
    )
    return {
        "total": len(entries),
        "good": len(good),
        "bad": len(bad),
        "average": average,
        "bad_entries": bad,
    }


def _stars(rating):
    return "★" * rating + "☆" * (5 - rating)


def _telegram_platform_section(platform):
    lines = [
        "{} <b>{}</b>".format(platform["icon"], html.escape(platform["name"])),
        "应用：{}".format(html.escape(str(platform["identifier"]))),
    ]
    if platform["error"]:
        lines.append("⚠️ 获取失败：{}".format(html.escape(platform["error"])))
        lines.append("")
        return lines

    summary = platform["summary"]
    lines.append("评论总数：{}".format(summary["total"]))
    lines.append("好评（≥4星）：{}".format(summary["good"]))
    lines.append("差评（&lt;4星）：{}".format(summary["bad"]))
    lines.append("平均评分：{:.1f}".format(summary["average"]))
    lines.append("")
    if summary["bad_entries"]:
        lines.append("⚠️ <b>差评列表</b>")
        for index, entry in enumerate(summary["bad_entries"], start=1):
            extra_value = entry.get(platform["extra_key"]) or "未知"
            lines.append(
                "{}. {} {} | {} | {}：{}".format(
                    index,
                    _stars(entry["rating"]),
                    html.escape(entry["author"]),
                    entry["modified_at"].strftime("%m-%d"),
                    platform["extra_label"],
                    html.escape(str(extra_value)),
                )
            )
            lines.append(html.escape(entry["text"] or "（无文字内容）"))
            lines.append("")
    else:
        lines.append("🎉 本周没有差评！")
        lines.append("")
    return lines


def build_telegram_report(android, ios, period_start, period_end):
    period = "{} ~ {}".format(
        period_start.strftime("%Y-%m-%d"), period_end.strftime("%Y-%m-%d")
    )
    lines = [
        "📊 <b>应用评价周报</b>",
        "周期：{}".format(period),
        "",
    ]
    lines.extend(_telegram_platform_section(android))
    lines.extend(_telegram_platform_section(ios))
    lines.append("<i>注：仅统计带文字内容的评论，纯星级评分两边接口都不提供。</i>")
    return "\n".join(lines)


def _markdown_platform_section(platform):
    lines = ["## {} {}".format(platform["icon"], platform["name"]), ""]
    lines.append("- 应用：`{}`".format(platform["identifier"]))
    if platform["error"]:
        lines.append("- ⚠️ 获取失败：{}".format(platform["error"]))
        lines.append("")
        return lines

    summary = platform["summary"]
    lines.append("")
    lines.append("| 指标 | 数量 |")
    lines.append("| --- | --- |")
    lines.append("| 评论总数 | {} |".format(summary["total"]))
    lines.append("| 好评（≥4星） | {} |".format(summary["good"]))
    lines.append("| 差评（<4星） | {} |".format(summary["bad"]))
    lines.append("| 平均评分 | {:.1f} |".format(summary["average"]))
    lines.append("")
    if summary["bad_entries"]:
        lines.append("### 差评列表")
        lines.append("")
        lines.append("| 日期 | 评分 | 用户 | {} | 内容 |".format(platform["extra_label"]))
        lines.append("| --- | --- | --- | --- | --- |")
        for entry in summary["bad_entries"]:
            text = (entry["text"] or "（无文字内容）").replace("|", "\\|")
            text = text.replace("\n", "<br>")
            extra_value = entry.get(platform["extra_key"]) or "未知"
            lines.append(
                "| {} | {}星 | {} | {} | {} |".format(
                    entry["modified_at"].strftime("%Y-%m-%d"),
                    entry["rating"],
                    entry["author"],
                    extra_value,
                    text,
                )
            )
    else:
        lines.append("本周没有差评 🎉")
    lines.append("")
    return lines


def build_markdown_report(android, ios, period_start, period_end):
    period = "{} ~ {}".format(
        period_start.strftime("%Y-%m-%d"), period_end.strftime("%Y-%m-%d")
    )
    lines = [
        "# 应用评价周报",
        "",
        "- 周期：{}".format(period),
        "",
    ]
    lines.extend(_markdown_platform_section(android))
    lines.extend(_markdown_platform_section(ios))
    lines.append("> 注：仅统计带文字内容的评论，纯星级评分两边接口都不提供。")
    return "\n".join(lines)


def chunk_message(text, limit=TELEGRAM_MESSAGE_LIMIT):
    """按行切分长消息，保证每段不超过 limit（Telegram 单条上限 4096 字符）。"""
    if len(text) <= limit:
        return [text]

    chunks = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else current + "\n" + line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python3 -m unittest test_report -v`
Expected: PASS（全部测试绿）

- [ ] **Step 5: 提交**

```bash
git add report.py test_report.py
git commit -m "feat: add platform-agnostic combined report generation"
```

---

### Task 3: iOS 抓取逻辑 `app_store.py`

**Files:**
- Modify: `requirements.txt`
- Create: `app_store.py`
- Test: `test_app_store.py`

**Interfaces:**
- Produces: `build_jwt(key_id: str, issuer_id: str, private_key_pem: str) -> str`
- Produces: `fetch_reviews(app_id: str, key_id: str, issuer_id: str, private_key_pem: str, since: datetime) -> list[dict]` — Apple `customerReviews` 原始 `data` 元素列表（可能混有个别早于 `since` 的条目，交给 `extract_entries` 过滤）。
- Produces: `extract_entries(reviews: list[dict], since: datetime) -> list[dict]` — entry 为 `{"author": str, "rating": int, "text": str, "territory": str, "modified_at": datetime}`，按 `modified_at` 倒序。
- Consumes: 无（自包含），但产出的 entry 字段名（`territory`）是 Task 2 平台字典契约里 `extra_key="territory"` 依赖的字段，必须一致。

- [ ] **Step 1: 新增依赖**

修改 `requirements.txt`：

```
google-api-python-client>=2.100.0
google-auth>=2.23.0
requests>=2.31.0
PyJWT[crypto]>=2.8.0
```

Run: `pip install -r requirements.txt`
Expected: 安装成功，无报错。

- [ ] **Step 2: 写失败的测试 `test_app_store.py`**

```python
"""app_store.py 的单元测试：JWT 构造 + customerReviews 解析（不打真实网络）。"""
import unittest
from datetime import datetime, timezone
from unittest import mock

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import app_store

SINCE = datetime(2026, 7, 8, tzinfo=timezone.utc)
IN_WINDOW_ISO = "2026-07-10T12:00:00Z"
OUT_OF_WINDOW_ISO = "2026-07-01T12:00:00Z"


def _generate_test_private_key_pem():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _apple_review(created_at_iso, rating, title, body, territory, nickname):
    return {
        "attributes": {
            "rating": rating,
            "title": title,
            "body": body,
            "reviewerNickname": nickname,
            "createdDate": created_at_iso,
            "territory": territory,
        }
    }


def _fake_response(payload):
    response = mock.Mock()
    response.raise_for_status = mock.Mock()
    response.json.return_value = payload
    return response


class BuildJwtTest(unittest.TestCase):
    def test_header_and_payload_fields(self):
        private_key_pem = _generate_test_private_key_pem()
        token = app_store.build_jwt("KEY123", "ISSUER456", private_key_pem)

        header = jwt.get_unverified_header(token)
        self.assertEqual(header["alg"], "ES256")
        self.assertEqual(header["kid"], "KEY123")
        self.assertEqual(header["typ"], "JWT")

        payload = jwt.decode(token, algorithms=["ES256"], options={"verify_signature": False})
        self.assertEqual(payload["iss"], "ISSUER456")
        self.assertEqual(payload["aud"], "appstoreconnect-v1")
        self.assertEqual(payload["exp"] - payload["iat"], 20 * 60)


class ExtractEntriesTest(unittest.TestCase):
    def test_keeps_reviews_within_window(self):
        reviews = [_apple_review(IN_WINDOW_ISO, 5, "Great", "Works well", "USA", "alice")]
        entries = app_store.extract_entries(reviews, since=SINCE)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["rating"], 5)
        self.assertEqual(entry["text"], "Great\nWorks well")
        self.assertEqual(entry["author"], "alice")
        self.assertEqual(entry["territory"], "USA")
        self.assertEqual(
            entry["modified_at"], datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        )

    def test_drops_reviews_before_window(self):
        reviews = [_apple_review(OUT_OF_WINDOW_ISO, 5, "Old", "review", "USA", "bob")]
        self.assertEqual(app_store.extract_entries(reviews, since=SINCE), [])

    def test_uses_body_only_when_title_missing(self):
        reviews = [_apple_review(IN_WINDOW_ISO, 4, "", "Just a body", "CHN", "carol")]
        entries = app_store.extract_entries(reviews, since=SINCE)
        self.assertEqual(entries[0]["text"], "Just a body")

    def test_missing_nickname_falls_back_to_anonymous(self):
        review = _apple_review(IN_WINDOW_ISO, 3, "Meh", "It's ok", "CHN", "placeholder")
        del review["attributes"]["reviewerNickname"]
        entries = app_store.extract_entries([review], since=SINCE)
        self.assertEqual(entries[0]["author"], "匿名用户")

    def test_sorted_newest_first(self):
        older = _apple_review(IN_WINDOW_ISO, 3, "older", "old", "USA", "dave")
        newer = _apple_review("2026-07-11T12:00:00Z", 5, "newer", "new", "USA", "erin")
        entries = app_store.extract_entries([older, newer], since=SINCE)
        self.assertEqual([e["text"] for e in entries], ["newer\nnew", "older\nold"])


class FetchReviewsPaginationTest(unittest.TestCase):
    def setUp(self):
        self.private_key_pem = _generate_test_private_key_pem()

    @mock.patch("app_store.requests.get")
    def test_stops_paginating_once_page_is_out_of_window(self, mock_get):
        page1 = _fake_response(
            {
                "data": [_apple_review(IN_WINDOW_ISO, 5, "Great", "Nice", "USA", "alice")],
                "links": {"next": "https://api.appstoreconnect.apple.com/v1/apps/123/customerReviews?cursor=abc"},
            }
        )
        page2 = _fake_response(
            {
                "data": [_apple_review(OUT_OF_WINDOW_ISO, 1, "Bad", "Meh", "USA", "bob")],
                "links": {},
            }
        )
        mock_get.side_effect = [page1, page2]

        reviews = app_store.fetch_reviews(
            "123", "KEY123", "ISSUER456", self.private_key_pem, since=SINCE,
        )

        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(len(reviews), 2)

    @mock.patch("app_store.requests.get")
    def test_stops_when_no_next_link(self, mock_get):
        page1 = _fake_response(
            {
                "data": [_apple_review(IN_WINDOW_ISO, 5, "Great", "Nice", "USA", "alice")],
                "links": {},
            }
        )
        mock_get.side_effect = [page1]

        reviews = app_store.fetch_reviews(
            "123", "KEY123", "ISSUER456", self.private_key_pem, since=SINCE,
        )

        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(len(reviews), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `python3 -m unittest test_app_store -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'app_store'`

- [ ] **Step 4: 实现 `app_store.py`**

```python
"""iOS（App Store Connect）评论拉取与解析：JWT 鉴权 + customerReviews 分页。"""
import time
from datetime import datetime

import jwt
import requests

API_BASE = "https://api.appstoreconnect.apple.com/v1"
JWT_TTL_SECONDS = 20 * 60


def build_jwt(key_id, issuer_id, private_key_pem):
    now = int(time.time())
    payload = {
        "iss": issuer_id,
        "iat": now,
        "exp": now + JWT_TTL_SECONDS,
        "aud": "appstoreconnect-v1",
    }
    headers = {"kid": key_id, "typ": "JWT"}
    return jwt.encode(payload, private_key_pem, algorithm="ES256", headers=headers)


def _parse_created_date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_reviews(app_id, key_id, issuer_id, private_key_pem, since):
    """分页拉取 customerReviews（按 -createdDate 排序），一旦当页最旧评论早于
    since 就停止翻页——Apple 接口保留全量历史数据，不像 Google 只留最近 7 天，
    需要主动截断，否则会一直翻到应用上线第一天。
    """
    token = build_jwt(key_id, issuer_id, private_key_pem)
    headers = {"Authorization": "Bearer {}".format(token)}
    url = "{}/apps/{}/customerReviews".format(API_BASE, app_id)
    params = {"sort": "-createdDate", "limit": 200}

    reviews = []
    while url:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        page = payload.get("data", [])
        reviews.extend(page)

        oldest_on_page = None
        for review in page:
            created_at = _parse_created_date(review["attributes"]["createdDate"])
            if oldest_on_page is None or created_at < oldest_on_page:
                oldest_on_page = created_at
        if oldest_on_page is not None and oldest_on_page < since:
            break

        url = payload.get("links", {}).get("next")
        params = None  # next 链接里已经带上了分页参数，不用再传一次
    return reviews


def extract_entries(reviews, since):
    """把 API 返回的评论转成扁平结构，只保留 since 之后的，按时间倒序。"""
    entries = []
    for review in reviews:
        attributes = review.get("attributes", {})
        created_at = _parse_created_date(attributes["createdDate"])
        if created_at < since:
            continue
        title = (attributes.get("title") or "").strip()
        body = (attributes.get("body") or "").strip()
        text = "{}\n{}".format(title, body).strip() if title else body
        entries.append(
            {
                "author": attributes.get("reviewerNickname") or "匿名用户",
                "rating": int(attributes.get("rating", 0)),
                "text": text,
                "territory": attributes.get("territory", ""),
                "modified_at": created_at,
            }
        )
    entries.sort(key=lambda e: e["modified_at"], reverse=True)
    return entries
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `python3 -m unittest test_app_store -v`
Expected: PASS（全部测试绿）

- [ ] **Step 6: 提交**

```bash
git add requirements.txt app_store.py test_app_store.py
git commit -m "feat: add App Store Connect review fetching via JWT-authenticated API"
```

---

### Task 4: 新入口 `review_summary.py`，下线旧脚本

**Files:**
- Create: `review_summary.py`
- Delete: `play_review_summary.py`
- Delete: `test_play_review_summary.py`

**Interfaces:**
- Consumes: `google_play.fetch_reviews/extract_entries`（Task 1）、`report.summarize/build_telegram_report/build_markdown_report/chunk_message`（Task 2）、`app_store.fetch_reviews/extract_entries`（Task 3）。
- Produces: `main()` 脚本入口；`require_env/get_env/fetch_android/fetch_ios/send_to_telegram/write_report_outputs` 为模块内部编排函数，供 `.github/workflows/weekly-review-summary.yml`（Task 5）调用 `python review_summary.py`。

`main()` 是 I/O 编排代码（读环境变量、发网络请求），和原脚本一样不写自动化测试，靠 Step 2 的手工冒烟测试验证。

- [ ] **Step 1: 实现 `review_summary.py`**

```python
"""每周汇总 Android + iOS 应用评价并推送到 Telegram。

环境变量：
- PLAY_SERVICE_ACCOUNT_JSON / PLAY_PACKAGE_NAME       Android 拉取凭证
- APPSTORE_KEY_ID / APPSTORE_ISSUER_ID /
  APPSTORE_PRIVATE_KEY / APPSTORE_APP_ID              iOS 拉取凭证
- TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID               推送目标（必需，缺失直接退出）

单一平台的凭证缺失或拉取失败，只影响该平台在报告里显示"获取失败"，
不影响另一平台正常统计和推送。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

import app_store
import google_play
import report

REVIEW_WINDOW_DAYS = 7
REPORT_FILE = "review_report.md"


def get_env(name):
    return os.environ.get(name, "").strip()


def require_env(name):
    value = get_env(name)
    if not value:
        print("缺少环境变量 {}，请检查 GitHub Secrets 配置。".format(name))
        sys.exit(1)
    return value


def fetch_android(period_start):
    package_name = get_env("PLAY_PACKAGE_NAME")
    service_account_json = get_env("PLAY_SERVICE_ACCOUNT_JSON")
    platform = {
        "icon": "📱",
        "name": "Android",
        "identifier": package_name or "（未配置）",
        "extra_key": "app_version",
        "extra_label": "版本",
    }
    if not package_name or not service_account_json:
        platform["summary"] = None
        platform["error"] = "缺少 PLAY_PACKAGE_NAME / PLAY_SERVICE_ACCOUNT_JSON 环境变量"
        return platform

    try:
        service_account_info = json.loads(service_account_json)
        reviews = google_play.fetch_reviews(package_name, service_account_info)
        entries = google_play.extract_entries(reviews, since=period_start)
        platform["summary"] = report.summarize(entries)
        platform["error"] = None
    except Exception as exc:  # noqa: BLE001 - 兜住任意拉取异常，不让 Android 故障拖垮 iOS 那部分
        platform["summary"] = None
        platform["error"] = str(exc)
    return platform


def fetch_ios(period_start):
    app_id = get_env("APPSTORE_APP_ID")
    key_id = get_env("APPSTORE_KEY_ID")
    issuer_id = get_env("APPSTORE_ISSUER_ID")
    private_key = get_env("APPSTORE_PRIVATE_KEY")
    platform = {
        "icon": "🍎",
        "name": "iOS",
        "identifier": app_id or "（未配置）",
        "extra_key": "territory",
        "extra_label": "地区",
    }
    if not all([app_id, key_id, issuer_id, private_key]):
        platform["summary"] = None
        platform["error"] = (
            "缺少 APPSTORE_APP_ID / APPSTORE_KEY_ID / "
            "APPSTORE_ISSUER_ID / APPSTORE_PRIVATE_KEY 环境变量"
        )
        return platform

    try:
        reviews = app_store.fetch_reviews(app_id, key_id, issuer_id, private_key, since=period_start)
        entries = app_store.extract_entries(reviews, since=period_start)
        platform["summary"] = report.summarize(entries)
        platform["error"] = None
    except Exception as exc:  # noqa: BLE001 - 兜住任意拉取异常，不让 iOS 故障拖垮 Android 那部分
        platform["summary"] = None
        platform["error"] = str(exc)
    return platform


def send_to_telegram(bot_token, chat_id, text):
    url = "https://api.telegram.org/bot{}/sendMessage".format(bot_token)
    for chunk in report.chunk_message(text):
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(
                "Telegram 发送失败（HTTP {}）：{}".format(
                    response.status_code, response.text
                )
            )


def write_report_outputs(markdown_report):
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(markdown_report)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(markdown_report + "\n")


def main():
    bot_token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")

    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=REVIEW_WINDOW_DAYS)

    print("拉取 Android 最近 {} 天的评论…".format(REVIEW_WINDOW_DAYS))
    android = fetch_android(period_start)
    if android["error"]:
        print("Android 拉取失败：{}".format(android["error"]))
    else:
        print(
            "Android 共 {} 条评论：好评 {}，差评 {}".format(
                android["summary"]["total"], android["summary"]["good"], android["summary"]["bad"]
            )
        )

    print("拉取 iOS 最近 {} 天的评论…".format(REVIEW_WINDOW_DAYS))
    ios = fetch_ios(period_start)
    if ios["error"]:
        print("iOS 拉取失败：{}".format(ios["error"]))
    else:
        print(
            "iOS 共 {} 条评论：好评 {}，差评 {}".format(
                ios["summary"]["total"], ios["summary"]["good"], ios["summary"]["bad"]
            )
        )

    markdown_report = report.build_markdown_report(android, ios, period_start, period_end)
    write_report_outputs(markdown_report)

    telegram_report = report.build_telegram_report(android, ios, period_start, period_end)
    send_to_telegram(bot_token, chat_id, telegram_report)
    print("已推送到 Telegram。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 手工冒烟测试 —— 缺少 Telegram 凭证时应直接退出，不崩溃**

Run:
```bash
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID PLAY_PACKAGE_NAME PLAY_SERVICE_ACCOUNT_JSON \
      APPSTORE_KEY_ID APPSTORE_ISSUER_ID APPSTORE_PRIVATE_KEY APPSTORE_APP_ID
python3 review_summary.py; echo "exit code: $?"
```
Expected:
```
缺少环境变量 TELEGRAM_BOT_TOKEN，请检查 GitHub Secrets 配置。
exit code: 1
```

- [ ] **Step 3: 运行完整测试套件，确认新增模块没有破坏既有测试**

Run: `python3 -m unittest discover -v`
Expected: PASS（`test_google_play` / `test_report` / `test_app_store` / 仍存在的 `test_play_review_summary` 全部通过）

- [ ] **Step 4: 提交新入口**

```bash
git add review_summary.py
git commit -m "feat: add combined Android + iOS entrypoint review_summary.py"
```

- [ ] **Step 5: 删除旧入口及其测试**

```bash
git rm play_review_summary.py test_play_review_summary.py
```

- [ ] **Step 6: 再次运行完整测试套件，确认删除后依然全绿**

Run: `python3 -m unittest discover -v`
Expected: PASS（只剩 `test_google_play` / `test_report` / `test_app_store` 三个测试文件，全部通过）

- [ ] **Step 7: 提交删除**

```bash
git commit -m "chore: remove superseded play_review_summary entrypoint"
```

---

### Task 5: GitHub Actions 工作流 + README 更新

**Files:**
- Modify: `.github/workflows/weekly-review-summary.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `review_summary.py`（Task 4）作为工作流实际执行的入口脚本。

- [ ] **Step 1: 更新 `.github/workflows/weekly-review-summary.yml`**

把整个文件内容替换为：

```yaml
name: Weekly App Review Summary

on:
  schedule:
    # 每周一 01:00 UTC（北京时间周一上午 09:00）
    - cron: "0 1 * * 1"
  workflow_dispatch: {}

permissions:
  contents: read

jobs:
  summarize:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run unit tests
        run: python -m unittest discover -v

      - name: Generate and send weekly review summary
        env:
          PLAY_SERVICE_ACCOUNT_JSON: ${{ secrets.PLAY_SERVICE_ACCOUNT_JSON }}
          PLAY_PACKAGE_NAME: ${{ secrets.PLAY_PACKAGE_NAME }}
          APPSTORE_KEY_ID: ${{ secrets.APPSTORE_KEY_ID }}
          APPSTORE_ISSUER_ID: ${{ secrets.APPSTORE_ISSUER_ID }}
          APPSTORE_PRIVATE_KEY: ${{ secrets.APPSTORE_PRIVATE_KEY }}
          APPSTORE_APP_ID: ${{ secrets.APPSTORE_APP_ID }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python review_summary.py

      - name: Upload report artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: review-report
          path: review_report.md
          if-no-files-found: ignore
          retention-days: 90
```

- [ ] **Step 2: 更新 `README.md`**

把整个文件内容替换为：

```markdown
# App 评价周报（Google Play + App Store）

每周一自动拉取 Android（Google Play）和 iOS（App Store Connect）应用最近 7 天的评论，统计好评/差评数量、平均分，分平台列出差评内容，合并推送到 Telegram，并在 GitHub Actions 运行页留存 Markdown 报告。

## 统计口径

- **好评**：≥ 4 星；**差评**：< 4 星。
- **只统计带文字内容的评论**。用户只打星、不写文字的纯评分，Google Play / App Store Connect 两边接口都不提供逐条数据，无法统计。
- Google Play 评论接口只保留最近约 7 天的数据，所以按周运行正好覆盖，不要停跑太久，否则中间的评论会取不到。App Store Connect 接口保留全量历史评论，按周期过滤即可，没有这个限制。
- 两个平台的拉取各自独立：其中一个平台配置有误或拉取失败，不影响另一个平台正常统计和推送，失败的那部分会在报告里标注"获取失败"。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `review_summary.py` | 主入口：读取环境变量 → 分别拉取两平台评论 → 生成合并报告 → 推送 Telegram |
| `google_play.py` | Android 评论拉取（Google Play Developer API） |
| `app_store.py` | iOS 评论拉取（App Store Connect API，JWT 鉴权） |
| `report.py` | 统计逻辑 + Telegram / Markdown 合并报告生成 |
| `test_google_play.py` / `test_app_store.py` / `test_report.py` | 单元测试（`python3 -m unittest discover` 运行） |
| `.github/workflows/weekly-review-summary.yml` | 定时任务：每周一北京时间 09:00 执行，也可手动触发 |
| `requirements.txt` | Python 依赖 |

## 配置步骤

### 1. Android：创建 Google Service Account 并授权

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 创建（或选择）一个项目，启用 **Google Play Android Developer API**。
2. 「IAM 与管理 → 服务账号」创建一个 service account，创建 JSON 密钥并下载。
3. 打开 [Google Play Console](https://play.google.com/console/) →「用户和权限」→ 邀请用户，填入 service account 的邮箱地址（形如 `xxx@yyy.iam.gserviceaccount.com`）。
4. 授予应用级权限：至少勾选 **「查看应用信息（只读）」** 和 **「回复评价」**（评论接口需要）。

### 2. iOS：创建 App Store Connect API Key

1. 打开 [App Store Connect](https://appstoreconnect.apple.com/) →「用户和访问」→「密钥」（Keys）。
2. 创建一个 API Key，角色至少需要能读取 Customer Reviews（如 **App Manager**，或自定义角色勾选「客户评论」权限）。
3. 记下 **Key ID** 和 **Issuer ID**，下载生成的 `.p8` 私钥文件（只能下载一次，务必妥善保存）。
4. 找到应用的数字 **App ID**：App Store Connect → 该 App →「App 信息」页面里的「Apple ID」（是一串数字，不是 bundle id）。

### 3. 创建 Telegram Bot

1. 在 Telegram 里找 [@BotFather](https://t.me/BotFather)，发送 `/newbot` 创建 bot，拿到 **bot token**。
2. 把 bot 拉进接收报告的群（或直接私聊 bot 发一条消息）。
3. 获取 **chat id**：浏览器访问 `https://api.telegram.org/bot<TOKEN>/getUpdates`，在返回 JSON 里找 `chat.id`（群的 id 一般是负数，形如 `-100xxxxxxxxxx`）。

### 4. 配置 GitHub Secrets

仓库「Settings → Secrets and variables → Actions → New repository secret」，添加以下 8 个：

| Secret 名 | 内容 |
| --- | --- |
| `PLAY_SERVICE_ACCOUNT_JSON` | service account JSON 文件的**完整内容**（整段粘贴） |
| `PLAY_PACKAGE_NAME` | 应用 package id，如 `com.example.app` |
| `APPSTORE_KEY_ID` | App Store Connect API Key 的 Key ID |
| `APPSTORE_ISSUER_ID` | Issuer ID |
| `APPSTORE_PRIVATE_KEY` | `.p8` 私钥文件的**完整内容**（整段粘贴） |
| `APPSTORE_APP_ID` | App Store Connect 里的数字 App ID（不是 bundle id） |
| `TELEGRAM_BOT_TOKEN` | BotFather 给的 token |
| `TELEGRAM_CHAT_ID` | 接收报告的 chat id |

### 5. 验证

推送代码到 GitHub 后，在「Actions → Weekly App Review Summary → Run workflow」手动触发一次，确认 Telegram 收到消息。之后每周一北京时间 09:00 自动运行。

## 修改定时时间

编辑 `.github/workflows/weekly-review-summary.yml` 里的 cron 表达式（**UTC 时间**，北京时间减 8 小时）：

```yaml
- cron: "0 1 * * 1"   # 周一 01:00 UTC = 北京时间周一 09:00
```

## 本地运行

```bash
pip install -r requirements.txt
python3 -m unittest discover -v      # 跑测试
export PLAY_SERVICE_ACCOUNT_JSON="$(cat service-account.json)"
export PLAY_PACKAGE_NAME="com.example.app"
export APPSTORE_KEY_ID="ABC123DEFG"
export APPSTORE_ISSUER_ID="12345678-1234-1234-1234-123456789012"
export APPSTORE_PRIVATE_KEY="$(cat AuthKey_ABC123DEFG.p8)"
export APPSTORE_APP_ID="123456789"
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="-100xxxxxxxxxx"
python3 review_summary.py
```
```

- [ ] **Step 3: 运行完整测试套件做最终确认**

Run: `python3 -m unittest discover -v`
Expected: PASS（全部测试绿）

- [ ] **Step 4: 提交**

```bash
git add .github/workflows/weekly-review-summary.yml README.md
git commit -m "docs: document iOS setup and update workflow for combined review summary"
```
