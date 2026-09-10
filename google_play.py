"""Android（Google Play）评论拉取与解析。"""
from datetime import datetime, timedelta, timezone


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


# ---------------------------------------------------------------------------
# 安装量：Play Console 没有安装量 API，只能读 Cloud Storage 报表桶里的月度 CSV。
# 官方说明数据"3 到 7 天内"发布，实测通常滞后 2～3 天，所以调用方按 D-2 取，
# 取不到时回退到更早的最新一天。
# ---------------------------------------------------------------------------

STORAGE_SCOPE = "https://www.googleapis.com/auth/devstorage.read_only"
STORAGE_OBJECT_URL = "https://storage.googleapis.com/storage/v1/b/{bucket}/o/{object}?alt=media"


def installs_report_object(package_name, month):
    """某个月的 installs overview CSV 在报表桶里的对象名。"""
    return "stats/installs/installs_{}_{}_overview.csv".format(
        package_name, month.strftime("%Y%m")
    )


def _to_int(value):
    value = (value or "").strip()
    return int(value) if value else 0


def parse_installs_csv(raw_bytes):
    """把 UTF-16 编码的 installs CSV 解析成 {date: {...}}，只保留本项目关心的三列。"""
    import csv
    import io
    from datetime import date

    reader = csv.DictReader(io.StringIO(raw_bytes.decode("utf-16")))
    rows = {}
    for record in reader:
        raw_date = (record.get("Date") or "").strip()
        if not raw_date:
            continue
        day = date.fromisoformat(raw_date)
        rows[day] = {
            "device_installs": _to_int(record.get("Daily Device Installs")),
            "device_uninstalls": _to_int(record.get("Daily Device Uninstalls")),
            "user_installs": _to_int(record.get("Daily User Installs")),
        }
    return rows


def make_storage_downloader(bucket, service_account_info):
    """返回 downloader(object_name) -> bytes | None（对象不存在时返回 None）。"""
    from urllib.parse import quote

    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info, scopes=[STORAGE_SCOPE]
    )
    session = AuthorizedSession(credentials)

    def download(object_name):
        url = STORAGE_OBJECT_URL.format(bucket=bucket, object=quote(object_name, safe=""))
        response = session.get(url, timeout=60)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    return download


def _previous_month(day):
    first = day.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def fetch_installs(package_name, target_date, downloader):
    """取 target_date 当天的安装量；该日尚未生成时回退到更早的最新一天。

    先读 target_date 所在月份的 CSV，不够再读上个月的（月初几天数据在上月文件里）。
    两个月都没有可用数据返回 None。返回值带 "date" 字段，调用方据此判断是否回退。
    """
    rows = {}
    for month in (target_date, _previous_month(target_date)):
        raw = downloader(installs_report_object(package_name, month))
        if raw:
            rows.update(parse_installs_csv(raw))
        if target_date in rows:
            return dict(rows[target_date], date=target_date)
        earlier = [day for day in rows if day < target_date]
        if earlier:
            latest = max(earlier)
            return dict(rows[latest], date=latest)
    return None
