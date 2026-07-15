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
