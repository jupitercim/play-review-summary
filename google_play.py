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
