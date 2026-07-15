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
    if summary["total"] == 0:
        lines.append("本周无评论")
        lines.append("")
        return lines

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
    if summary["total"] == 0:
        lines.append("")
        lines.append("本周无评论")
        lines.append("")
        return lines

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
