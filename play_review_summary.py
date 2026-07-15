"""每周汇总 Google Play 应用评价并推送到 Telegram。

统计口径：Google Play Developer API 只返回带文字内容的评论，
纯星级评分（用户只打星不写字）不在统计范围内。

环境变量（由 GitHub Actions Secrets 注入）：
- PLAY_SERVICE_ACCOUNT_JSON  Google service account 的 JSON 文件内容
- PLAY_PACKAGE_NAME          应用的 package id，如 com.example.app
- TELEGRAM_BOT_TOKEN         Telegram bot token（BotFather 创建）
- TELEGRAM_CHAT_ID           接收报告的 chat id（群或个人）
"""
import html
import json
import os
import sys
from datetime import datetime, timedelta, timezone

GOOD_RATING_THRESHOLD = 4  # >= 4 星算好评，< 4 星算差评
REVIEW_WINDOW_DAYS = 7
TELEGRAM_MESSAGE_LIMIT = 4096
REPORT_FILE = "review_report.md"


# ---------------------------------------------------------------------------
# 数据获取
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 纯逻辑：解析、统计、生成报告
# ---------------------------------------------------------------------------

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


def build_telegram_report(package_name, summary, period_start, period_end):
    """生成 Telegram HTML 格式（parse_mode=HTML）的周报。"""
    period = "{} ~ {}".format(
        period_start.strftime("%Y-%m-%d"), period_end.strftime("%Y-%m-%d")
    )
    lines = [
        "📊 <b>Google Play 评价周报</b>",
        "应用：{}".format(html.escape(package_name)),
        "周期：{}".format(period),
        "",
        "评论总数：{}".format(summary["total"]),
        "好评（≥4星）：{}".format(summary["good"]),
        "差评（&lt;4星）：{}".format(summary["bad"]),
        "平均评分：{:.1f}".format(summary["average"]),
        "",
    ]
    if summary["bad_entries"]:
        lines.append("⚠️ <b>差评列表</b>")
        for index, entry in enumerate(summary["bad_entries"], start=1):
            version = entry["app_version"] or "未知版本"
            lines.append(
                "{}. {} {} | {} | v{}".format(
                    index,
                    _stars(entry["rating"]),
                    html.escape(entry["author"]),
                    entry["modified_at"].strftime("%m-%d"),
                    html.escape(version),
                )
            )
            lines.append(html.escape(entry["text"] or "（无文字内容）"))
            lines.append("")
    else:
        lines.append("🎉 本周没有差评！")
    lines.append("")
    lines.append("<i>注：仅统计带文字内容的评论，纯星级评分 Google API 不提供。</i>")
    return "\n".join(lines)


def build_markdown_report(package_name, summary, period_start, period_end):
    """生成 Markdown 格式的周报（写入 GitHub Actions Summary / 附件）。"""
    period = "{} ~ {}".format(
        period_start.strftime("%Y-%m-%d"), period_end.strftime("%Y-%m-%d")
    )
    lines = [
        "# Google Play 评价周报",
        "",
        "- 应用：`{}`".format(package_name),
        "- 周期：{}".format(period),
        "",
        "| 指标 | 数量 |",
        "| --- | --- |",
        "| 评论总数 | {} |".format(summary["total"]),
        "| 好评（≥4星） | {} |".format(summary["good"]),
        "| 差评（<4星） | {} |".format(summary["bad"]),
        "| 平均评分 | {:.1f} |".format(summary["average"]),
        "",
    ]
    if summary["bad_entries"]:
        lines.append("## 差评列表")
        lines.append("")
        lines.append("| 日期 | 评分 | 用户 | 版本 | 内容 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for entry in summary["bad_entries"]:
            text = (entry["text"] or "（无文字内容）").replace("|", "\\|")
            text = text.replace("\n", "<br>")
            lines.append(
                "| {} | {}星 | {} | {} | {} |".format(
                    entry["modified_at"].strftime("%Y-%m-%d"),
                    entry["rating"],
                    entry["author"],
                    entry["app_version"] or "未知",
                    text,
                )
            )
    else:
        lines.append("本周没有差评 🎉")
    lines.append("")
    lines.append("> 注：仅统计带文字内容的评论，纯星级评分 Google API 不提供。")
    return "\n".join(lines)


def chunk_message(text, limit=TELEGRAM_MESSAGE_LIMIT):
    """按行切分长消息，保证每段不超过 limit（Telegram 单条上限 4096 字符）。"""
    if len(text) <= limit:
        return [text]

    chunks = []
    current = ""
    for line in text.split("\n"):
        # 单行本身超长时硬切
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


# ---------------------------------------------------------------------------
# 推送与输出
# ---------------------------------------------------------------------------

def send_to_telegram(bot_token, chat_id, text):
    import requests

    url = "https://api.telegram.org/bot{}/sendMessage".format(bot_token)
    for chunk in chunk_message(text):
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
    """写报告文件（供 Actions 上传附件），并追加到 Job Summary。"""
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(markdown_report)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(markdown_report + "\n")


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        print("缺少环境变量 {}，请检查 GitHub Secrets 配置。".format(name))
        sys.exit(1)
    return value


def main():
    package_name = require_env("PLAY_PACKAGE_NAME")
    service_account_json = require_env("PLAY_SERVICE_ACCOUNT_JSON")
    bot_token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")

    try:
        service_account_info = json.loads(service_account_json)
    except json.JSONDecodeError as error:
        print("PLAY_SERVICE_ACCOUNT_JSON 不是合法的 JSON：{}".format(error))
        sys.exit(1)

    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=REVIEW_WINDOW_DAYS)

    print("拉取 {} 最近 {} 天的评论…".format(package_name, REVIEW_WINDOW_DAYS))
    reviews = fetch_reviews(package_name, service_account_info)
    entries = extract_entries(reviews, since=period_start)
    summary = summarize(entries)
    print(
        "共 {} 条评论：好评 {}，差评 {}".format(
            summary["total"], summary["good"], summary["bad"]
        )
    )

    markdown_report = build_markdown_report(
        package_name, summary, period_start, period_end
    )
    write_report_outputs(markdown_report)

    telegram_report = build_telegram_report(
        package_name, summary, period_start, period_end
    )
    send_to_telegram(bot_token, chat_id, telegram_report)
    print("已推送到 Telegram。")


if __name__ == "__main__":
    main()
