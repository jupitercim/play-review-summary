"""平台无关的每日安装量报告：把 Android / iOS 的指标渲染成 Telegram / Markdown 文本。

平台结果字典（android / ios 参数）的形状：
{
    "icon": "📱" 或 "🍎",
    "name": "Android" 或 "iOS",
    "identifier": 应用标识（package name 或 App Store Connect 数字 App ID）,
    "metrics": [(中文指标名, 整数), ...]，该日数据尚未生成时为 None,
    "data_date": metrics 对应的日期；回退到更早一天时早于 target_date，无数据时为 None,
    "error": 错误信息字符串，成功时为 None,
}
"""
import html

DATA_LAG_DAYS = 2

FOOTNOTE = (
    "注：Android 数据来自 Play Console 统计报表（用户安装按用户计，设备安装含重装）；"
    "iOS 数据来自 Sales and Trends 日报，按太平洋时间切日，首次下载不含重新下载。"
)


def _fallback_note(platform, target_date):
    data_date = platform["data_date"]
    if data_date and data_date != target_date:
        return "{} 数据尚未生成，以下为最新可用的 {} 数据".format(
            target_date.isoformat(), data_date.isoformat()
        )
    return None


def _telegram_platform_section(platform, target_date):
    lines = [
        "{} <b>{}</b>".format(platform["icon"], html.escape(platform["name"])),
        "应用：{}".format(html.escape(str(platform["identifier"]))),
    ]
    if platform["error"]:
        lines.append("⚠️ 获取失败：{}".format(html.escape(platform["error"])))
    elif not platform["metrics"]:
        lines.append("该日数据尚未生成")
    else:
        note = _fallback_note(platform, target_date)
        if note:
            lines.append("⚠️ {}".format(note))
        for label, value in platform["metrics"]:
            lines.append("{}：{}".format(label, value))
    lines.append("")
    return lines


def build_telegram_report(android, ios, target_date):
    lines = [
        "📈 <b>应用每日安装量</b>",
        "日期：{}（数据延迟 {} 天）".format(target_date.isoformat(), DATA_LAG_DAYS),
        "",
    ]
    lines.extend(_telegram_platform_section(android, target_date))
    lines.extend(_telegram_platform_section(ios, target_date))
    lines.append("<i>{}</i>".format(FOOTNOTE))
    return "\n".join(lines)


def _markdown_platform_section(platform, target_date):
    lines = ["## {} {}".format(platform["icon"], platform["name"]), ""]
    lines.append("- 应用：`{}`".format(platform["identifier"]))
    if platform["error"]:
        lines.append("- ⚠️ 获取失败：{}".format(platform["error"]))
        lines.append("")
        return lines
    if not platform["metrics"]:
        lines.append("- 该日数据尚未生成")
        lines.append("")
        return lines

    note = _fallback_note(platform, target_date)
    if note:
        lines.append("- ⚠️ {}".format(note))
    lines.append("")
    lines.append("| 指标 | 数量 |")
    lines.append("| --- | --- |")
    for label, value in platform["metrics"]:
        lines.append("| {} | {} |".format(label, value))
    lines.append("")
    return lines


def build_markdown_report(android, ios, target_date):
    lines = [
        "# 应用每日安装量",
        "",
        "- 日期：{}（数据延迟 {} 天）".format(target_date.isoformat(), DATA_LAG_DAYS),
        "",
    ]
    lines.extend(_markdown_platform_section(android, target_date))
    lines.extend(_markdown_platform_section(ios, target_date))
    lines.append("> {}".format(FOOTNOTE))
    return "\n".join(lines)
