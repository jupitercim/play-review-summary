"""每天汇总 Android + iOS 前天（D-2）的应用安装量并推送到 Telegram。

为什么是 D-2 而不是前一天：
- Google Play 安装量只能从 Play Console 报表桶读，官方说明数据 3～7 天内发布，实测滞后 2～3 天。
- App Store 每日销售报告按太平洋时间切日，"generally available by 8 a.m. PT"，
  即北京时间约 23:00～00:00 才有前一天的数据；北京早上 9 点跑任务时最新只有 D-2。

环境变量：
- PLAY_SERVICE_ACCOUNT_JSON / PLAY_PACKAGE_NAME / PLAY_REPORTS_BUCKET        Android
- APPSTORE_KEY_ID / APPSTORE_ISSUER_ID / APPSTORE_PRIVATE_KEY /
  APPSTORE_APP_ID / APPSTORE_VENDOR_NUMBER                                    iOS
- TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID                                      推送目标（必需，缺失直接退出）

单一平台的凭证缺失、拉取失败或数据尚未生成，只影响该平台在报告里的展示，不影响另一平台。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import app_store
import google_play
import install_report
import notify

REPORT_FILE = "install_report.md"


def get_env(name):
    return os.environ.get(name, "").strip()


def require_env(name):
    value = get_env(name)
    if not value:
        print("缺少环境变量 {}，请检查 GitHub Secrets 配置。".format(name))
        sys.exit(1)
    return value


def target_date(now=None):
    now = now or datetime.now(timezone.utc)
    return now.date() - timedelta(days=install_report.DATA_LAG_DAYS)


def normalize_bucket(value):
    """Play Console 复制出来的是 gs://pubsite_prod_rev_xxx/ 形式，接口只要桶名。"""
    value = value.strip()
    if value.startswith("gs://"):
        value = value[len("gs://"):]
    return value.strip("/")


def _platform(icon, name, identifier):
    return {
        "icon": icon,
        "name": name,
        "identifier": identifier or "（未配置）",
        "metrics": None,
        "data_date": None,
        "error": None,
    }


def fetch_android(target):
    package_name = get_env("PLAY_PACKAGE_NAME")
    service_account_json = get_env("PLAY_SERVICE_ACCOUNT_JSON")
    bucket = get_env("PLAY_REPORTS_BUCKET")
    platform = _platform("📱", "Android", package_name)
    if not all([package_name, service_account_json, bucket]):
        platform["error"] = (
            "缺少 PLAY_PACKAGE_NAME / PLAY_SERVICE_ACCOUNT_JSON / PLAY_REPORTS_BUCKET 环境变量"
        )
        return platform

    try:
        downloader = google_play.make_storage_downloader(
            normalize_bucket(bucket), json.loads(service_account_json)
        )
        result = google_play.fetch_installs(package_name, target, downloader)
        if result is not None:
            platform["metrics"] = [
                ("用户安装", result["user_installs"]),
                ("设备安装", result["device_installs"]),
                ("设备卸载", result["device_uninstalls"]),
            ]
            platform["data_date"] = result["date"]
    except Exception as exc:  # noqa: BLE001 - 兜住任意拉取异常，不让 Android 故障拖垮 iOS 那部分
        platform["error"] = str(exc)
    return platform


def fetch_ios(target):
    app_id = get_env("APPSTORE_APP_ID")
    key_id = get_env("APPSTORE_KEY_ID")
    issuer_id = get_env("APPSTORE_ISSUER_ID")
    private_key = get_env("APPSTORE_PRIVATE_KEY")
    vendor_number = get_env("APPSTORE_VENDOR_NUMBER")
    platform = _platform("🍎", "iOS", app_id)
    if not all([app_id, key_id, issuer_id, private_key, vendor_number]):
        platform["error"] = (
            "缺少 APPSTORE_APP_ID / APPSTORE_KEY_ID / APPSTORE_ISSUER_ID / "
            "APPSTORE_PRIVATE_KEY / APPSTORE_VENDOR_NUMBER 环境变量"
        )
        return platform

    try:
        rows = app_store.fetch_daily_sales_rows(vendor_number, target, key_id, issuer_id, private_key)
        totals = app_store.summarize_downloads(rows, app_id)
        platform["metrics"] = [
            ("首次下载", totals["first_downloads"]),
            ("重新下载", totals["redownloads"]),
            ("更新", totals["updates"]),
        ]
        platform["data_date"] = target
    except Exception as exc:  # noqa: BLE001 - 兜住任意拉取异常，不让 iOS 故障拖垮 Android 那部分
        platform["error"] = str(exc)
    return platform


def _log_platform(platform):
    if platform["error"]:
        print("{} 拉取失败：{}".format(platform["name"], platform["error"]))
    elif not platform["metrics"]:
        print("{} 该日数据尚未生成".format(platform["name"]))
    else:
        summary = "，".join("{} {}".format(label, value) for label, value in platform["metrics"])
        print("{}（{}）：{}".format(platform["name"], platform["data_date"], summary))


def main():
    bot_token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")

    target = target_date()
    print("统计 {} 的安装量（D-{}）…".format(target, install_report.DATA_LAG_DAYS))

    android = fetch_android(target)
    _log_platform(android)
    ios = fetch_ios(target)
    _log_platform(ios)

    markdown_report = install_report.build_markdown_report(android, ios, target)
    notify.write_report_outputs(markdown_report, REPORT_FILE)

    telegram_report = install_report.build_telegram_report(android, ios, target)
    notify.send_to_telegram(bot_token, chat_id, telegram_report)
    print("已推送到 Telegram。")


if __name__ == "__main__":
    main()
