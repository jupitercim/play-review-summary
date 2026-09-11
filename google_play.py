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


def _storage_error_message(response):
    """Cloud Storage 的错误体是 {"error": {"message": ...}}，里面会写清是哪个账号缺哪个权限，
    或者是项目没启用 Cloud Storage JSON API；拿不到 JSON 就退回原始文本。"""
    try:
        message = response.json()["error"]["message"]
        if message:
            return str(message).strip()
    except Exception:  # noqa: BLE001 - 非 JSON 错误体
        pass
    return (response.text or "").strip()


def make_storage_downloader(bucket, service_account_info, session=None):
    """返回 downloader(object_name) -> bytes | None（对象不存在时返回 None）。

    session 参数供测试注入；不传时用 service account 凭证建 AuthorizedSession。
    """
    from urllib.parse import quote

    if session is None:
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
            print("报表桶 {}：{} 不存在（HTTP 404）".format(bucket, object_name))
            return None
        if response.status_code != 200:
            raise RuntimeError(
                "读取报表桶 {} 失败（HTTP {}）：{}".format(
                    bucket, response.status_code, _storage_error_message(response)
                )
            )
        print("报表桶 {}：{} 已下载（{} 字节）".format(bucket, object_name, len(response.content)))
        return response.content

    return download


def _previous_month(day):
    first = day.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


class ReportNotFound(RuntimeError):
    """报表桶里一个对应的 CSV 都找不到：几乎肯定是桶名、package name 或权限配置问题，
    而不是数据延迟（数据延迟时文件存在、只是缺最近几天的行）。"""


def _fetch_daily_report(object_for_month, parse, target_date, downloader):
    """月度 CSV 报表的通用读取：取 target_date 当天一行；该日尚未生成时回退到更早的最新一天。

    先读 target_date 所在月份的文件，不够再读上个月的（月初几天数据在上月文件里）。
    文件存在但两个月都没有可用行返回 None；两个文件都不存在抛 ReportNotFound。
    返回值带 "date" 字段，调用方据此判断是否回退。
    """
    rows = {}
    missing = []
    for month in (target_date, _previous_month(target_date)):
        object_name = object_for_month(month)
        raw = downloader(object_name)
        if raw is None:
            missing.append(object_name)
        else:
            rows.update(parse(raw))
        if target_date in rows:
            return dict(rows[target_date], date=target_date)
        earlier = [day for day in rows if day < target_date]
        if earlier:
            latest = max(earlier)
            return dict(rows[latest], date=latest)
    if len(missing) == 2:
        raise ReportNotFound(
            "报表桶里找不到 {} 和 {}，请检查 PLAY_REPORTS_BUCKET（只需要桶名）、"
            "PLAY_PACKAGE_NAME 以及 service account 的账号级批量报告权限。".format(*missing)
        )
    return None


def fetch_installs(package_name, target_date, downloader):
    """installs 报表：target_date 当天的用户/设备安装量，见 _fetch_daily_report 的回退规则。"""
    return _fetch_daily_report(
        lambda month: installs_report_object(package_name, month),
        parse_installs_csv,
        target_date,
        downloader,
    )


# ---------------------------------------------------------------------------
# 商店表现（store_performance）：详情页访客 / 商店获取用户，按国家维度的文件逐日求和。
# 口径：访客 = 访问过详情页且当时没装过的用户；获取 = 访问详情页后安装、且此前任何设备都
# 没装过的用户。和 installs 报表相比不含多设备安装和无详情页安装，但目前更新正常。
# ---------------------------------------------------------------------------


def store_performance_report_object(package_name, month):
    return "stats/store_performance/store_performance_{}_{}_country.csv".format(
        package_name, month.strftime("%Y%m")
    )


def parse_store_performance_csv(raw_bytes):
    """把 UTF-16 的 store_performance country CSV 按日期汇总成 {date: {"acquisitions", "visitors"}}。"""
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
        totals = rows.setdefault(day, {"acquisitions": 0, "visitors": 0})
        totals["acquisitions"] += _to_int(record.get("Store listing acquisitions"))
        totals["visitors"] += _to_int(record.get("Store listing visitors"))
    return rows


def fetch_store_performance(package_name, target_date, downloader):
    """store_performance 报表：target_date 当天的详情页访客与商店获取用户，见 _fetch_daily_report 的回退规则。"""
    return _fetch_daily_report(
        lambda month: store_performance_report_object(package_name, month),
        parse_store_performance_csv,
        target_date,
        downloader,
    )
