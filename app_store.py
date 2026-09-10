"""iOS（App Store Connect）评论拉取与解析：JWT 鉴权 + customerReviews 分页。"""
import csv
import gzip
import io
import re
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


# ---------------------------------------------------------------------------
# 下载量：Sales and Trends 每日销售报告（salesReports 接口）。
# 报告按太平洋时间切日，"generally available by 8 a.m. PT"，即北京时间约 23:00～00:00
# 才能拿到前一天的数据，所以调用方按 D-2 取。
# ---------------------------------------------------------------------------

SALES_REPORT_VERSION = "1_1"
# Product Type Identifier 归类，见 Apple「Product type identifiers」参考页。
FIRST_DOWNLOAD_TYPES = {"1", "1-B", "1E", "1EP", "1EU", "1F", "1T", "F1", "F1-B"}
REDOWNLOAD_TYPES = {"3", "3F"}
UPDATE_TYPES = {"7", "7F", "7T", "F7"}

_LATEST_VERSION_PATTERN = re.compile(r"latest version for this report is ([0-9_]+)")


def _error_detail(response):
    try:
        errors = response.json().get("errors", [])
        detail = "; ".join(
            (error.get("detail") or error.get("title") or "").strip() for error in errors
        )
        if detail:
            return detail
    except Exception:  # noqa: BLE001 - 非 JSON 错误体（例如 Apple 偶发的 HTML 400 页）
        pass
    return (response.text or "").strip()


def _parse_sales_tsv(gzipped):
    text = gzip.decompress(gzipped).decode("utf-8")
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def fetch_daily_sales_rows(vendor_number, report_date, key_id, issuer_id, private_key_pem):
    """拉取某一天的 SALES / SUMMARY / DAILY 报告，返回 TSV 行（dict 列表）。

    - 当天零销售：Apple 返回 404 "There were no sales for the date specified."，视为空列表。
    - version 过期：Apple 在 400 里告知最新版本号，按它说的重试一次。
    - 其他错误（报告尚未生成、权限不足等）：抛 RuntimeError，带上 Apple 的错误说明。
    """
    token = build_jwt(key_id, issuer_id, private_key_pem)
    headers = {"Authorization": "Bearer {}".format(token)}
    url = "{}/salesReports".format(API_BASE)
    version = SALES_REPORT_VERSION

    for attempt in range(2):
        params = {
            "filter[frequency]": "DAILY",
            "filter[reportDate]": report_date.strftime("%Y-%m-%d"),
            "filter[reportType]": "SALES",
            "filter[reportSubType]": "SUMMARY",
            "filter[vendorNumber]": str(vendor_number),
            "filter[version]": version,
        }
        response = requests.get(url, headers=headers, params=params, timeout=60)
        if response.status_code == 200:
            return _parse_sales_tsv(response.content)

        detail = _error_detail(response)
        if response.status_code == 404 and "no sales" in detail.lower():
            return []
        match = _LATEST_VERSION_PATTERN.search(detail)
        if response.status_code == 400 and match and attempt == 0:
            version = match.group(1)
            continue
        raise RuntimeError(
            "App Store 销售报告获取失败（HTTP {}）：{}".format(response.status_code, detail)
        )


def _units(value):
    value = (value or "").strip()
    return int(float(value)) if value else 0


def summarize_downloads(rows, app_id):
    """按 Product Type 把某个 App 的 Units 归类成首次下载 / 重新下载 / 更新。"""
    totals = {"first_downloads": 0, "redownloads": 0, "updates": 0}
    for row in rows:
        if (row.get("Apple Identifier") or "").strip() != str(app_id):
            continue
        product_type = (row.get("Product Type Identifier") or "").strip()
        units = _units(row.get("Units"))
        if product_type in FIRST_DOWNLOAD_TYPES:
            totals["first_downloads"] += units
        elif product_type in REDOWNLOAD_TYPES:
            totals["redownloads"] += units
        elif product_type in UPDATE_TYPES:
            totals["updates"] += units
    return totals
