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
