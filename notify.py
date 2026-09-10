"""Telegram 推送，周报（review_summary.py）和日报（install_summary.py）共用。"""
import requests

import report

TELEGRAM_SEND_MESSAGE_URL = "https://api.telegram.org/bot{}/sendMessage"


def send_to_telegram(bot_token, chat_id, text):
    """按 Telegram 单条 4096 字符上限切块后依次发送，任一块失败即抛 RuntimeError。"""
    url = TELEGRAM_SEND_MESSAGE_URL.format(bot_token)
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
                "Telegram 发送失败（HTTP {}）：{}".format(response.status_code, response.text)
            )


def write_report_outputs(markdown_report, report_file):
    """把 Markdown 报告写到本地文件（供 workflow 上传为 artifact），
    并追加到 GitHub Actions 的 step summary（本地运行时没有该环境变量，跳过）。"""
    import os

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(markdown_report)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(markdown_report + "\n")
