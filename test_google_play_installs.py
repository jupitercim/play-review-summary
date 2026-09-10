"""google_play.py 安装量部分的单元测试（不依赖 Google API / 网络）。"""
import unittest
from datetime import date

import google_play


def make_installs_csv(rows):
    """按 Play Console 导出格式构造 UTF-16 编码的 installs overview CSV。"""
    header = (
        "Date,Package Name,Daily Device Installs,Daily Device Uninstalls,"
        "Daily Device Upgrades,Total User Installs,Daily User Installs,"
        "Daily User Uninstalls,Active Device Installs"
    )
    lines = [header]
    for day, device_installs, device_uninstalls, user_installs in rows:
        lines.append(
            "{},com.example.app,{},{},5,1000,{},2,900".format(
                day, device_installs, device_uninstalls, user_installs
            )
        )
    return ("\r\n".join(lines) + "\r\n").encode("utf-16")


class InstallsReportObjectTest(unittest.TestCase):
    def test_builds_overview_object_name_for_month(self):
        name = google_play.installs_report_object("com.example.app", date(2026, 9, 8))
        self.assertEqual(
            name, "stats/installs/installs_com.example.app_202609_overview.csv"
        )


class ParseInstallsCsvTest(unittest.TestCase):
    def test_parses_utf16_csv_into_rows_keyed_by_date(self):
        raw = make_installs_csv([("2026-09-07", 130, 20, 123), ("2026-09-08", 140, 25, 131)])
        rows = google_play.parse_installs_csv(raw)
        self.assertEqual(sorted(rows), [date(2026, 9, 7), date(2026, 9, 8)])
        self.assertEqual(
            rows[date(2026, 9, 8)],
            {"device_installs": 140, "device_uninstalls": 25, "user_installs": 131},
        )

    def test_blank_numeric_cells_count_as_zero(self):
        raw = make_installs_csv([("2026-09-08", "", "", "")])
        rows = google_play.parse_installs_csv(raw)
        self.assertEqual(
            rows[date(2026, 9, 8)],
            {"device_installs": 0, "device_uninstalls": 0, "user_installs": 0},
        )


class FetchInstallsTest(unittest.TestCase):
    """fetch_installs 通过注入 downloader（object name -> bytes 或 None）隔离网络。"""

    def _downloader(self, files):
        calls = []

        def download(object_name):
            calls.append(object_name)
            return files.get(object_name)

        download.calls = calls
        return download

    def test_returns_target_date_row_when_present(self):
        september = "stats/installs/installs_com.example.app_202609_overview.csv"
        download = self._downloader(
            {september: make_installs_csv([("2026-09-07", 130, 20, 123), ("2026-09-08", 140, 25, 131)])}
        )
        result = google_play.fetch_installs(
            "com.example.app", date(2026, 9, 8), downloader=download
        )
        self.assertEqual(
            result,
            {"date": date(2026, 9, 8), "device_installs": 140, "device_uninstalls": 25, "user_installs": 131},
        )
        self.assertEqual(download.calls, [september])

    def test_falls_back_to_latest_earlier_date_when_target_missing(self):
        september = "stats/installs/installs_com.example.app_202609_overview.csv"
        download = self._downloader(
            {september: make_installs_csv([("2026-09-06", 100, 10, 90), ("2026-09-07", 130, 20, 123)])}
        )
        result = google_play.fetch_installs(
            "com.example.app", date(2026, 9, 8), downloader=download
        )
        self.assertEqual(result["date"], date(2026, 9, 7))
        self.assertEqual(result["user_installs"], 123)

    def test_ignores_rows_newer_than_target_when_falling_back(self):
        september = "stats/installs/installs_com.example.app_202609_overview.csv"
        download = self._downloader(
            {september: make_installs_csv([("2026-09-06", 100, 10, 90), ("2026-09-09", 130, 20, 123)])}
        )
        result = google_play.fetch_installs(
            "com.example.app", date(2026, 9, 8), downloader=download
        )
        self.assertEqual(result["date"], date(2026, 9, 6))

    def test_looks_into_previous_month_when_current_month_has_nothing_usable(self):
        september = "stats/installs/installs_com.example.app_202609_overview.csv"
        august = "stats/installs/installs_com.example.app_202608_overview.csv"
        download = self._downloader(
            {august: make_installs_csv([("2026-08-30", 100, 10, 90), ("2026-08-31", 110, 12, 95)])}
        )
        result = google_play.fetch_installs(
            "com.example.app", date(2026, 9, 1), downloader=download
        )
        self.assertEqual(result["date"], date(2026, 8, 31))
        self.assertEqual(download.calls, [september, august])

    def test_raises_naming_missing_files_when_no_report_exists_at_all(self):
        download = self._downloader({})
        with self.assertRaises(google_play.InstallsReportNotFound) as ctx:
            google_play.fetch_installs("com.example.app", date(2026, 9, 8), downloader=download)
        message = str(ctx.exception)
        self.assertIn("installs_com.example.app_202609_overview.csv", message)
        self.assertIn("installs_com.example.app_202608_overview.csv", message)
        self.assertIn("PLAY_REPORTS_BUCKET", message)

    def test_returns_none_when_files_exist_but_have_no_usable_rows(self):
        september = "stats/installs/installs_com.example.app_202609_overview.csv"
        august = "stats/installs/installs_com.example.app_202608_overview.csv"
        download = self._downloader({september: make_installs_csv([]), august: make_installs_csv([])})
        result = google_play.fetch_installs(
            "com.example.app", date(2026, 9, 8), downloader=download
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()


class StorageDownloaderTest(unittest.TestCase):
    """make_storage_downloader 通过注入 session 隔离鉴权和网络。"""

    def _session(self, status_code, content=b"", json_body=None, text=""):
        import contextlib
        import io
        from unittest import mock

        response = mock.Mock()
        response.status_code = status_code
        response.content = content
        response.text = text
        if json_body is None:
            response.json.side_effect = ValueError("not json")
        else:
            response.json.return_value = json_body
        session = mock.Mock()
        session.get.return_value = response
        self._quiet = contextlib.redirect_stdout(io.StringIO())
        return session

    def test_returns_content_and_requests_encoded_object_url(self):
        session = self._session(200, content=b"csv-bytes")
        download = google_play.make_storage_downloader("pubsite_prod_rev_1", None, session=session)
        with self._quiet:
            result = download("stats/installs/installs_com.example.app_202609_overview.csv")
        self.assertEqual(result, b"csv-bytes")
        url = session.get.call_args.args[0]
        self.assertEqual(
            url,
            "https://storage.googleapis.com/storage/v1/b/pubsite_prod_rev_1/o/"
            "stats%2Finstalls%2Finstalls_com.example.app_202609_overview.csv?alt=media",
        )

    def test_404_means_missing_file(self):
        session = self._session(404, json_body={"error": {"code": 404, "message": "No such object"}})
        download = google_play.make_storage_downloader("pubsite_prod_rev_1", None, session=session)
        with self._quiet:
            self.assertIsNone(download("stats/installs/x.csv"))

    def test_403_raises_with_cloud_storage_message(self):
        gcs_message = (
            "bot@proj.iam.gserviceaccount.com does not have storage.objects.get access to the "
            "Google Cloud Storage object. Permission 'storage.objects.get' denied on resource (or it may not exist)."
        )
        session = self._session(403, json_body={"error": {"code": 403, "message": gcs_message}})
        download = google_play.make_storage_downloader("pubsite_prod_rev_1", None, session=session)
        with self._quiet, self.assertRaises(RuntimeError) as ctx:
            download("stats/installs/x.csv")
        message = str(ctx.exception)
        self.assertIn("403", message)
        self.assertIn("storage.objects.get", message)
        self.assertIn("pubsite_prod_rev_1", message)

    def test_non_json_error_body_falls_back_to_text(self):
        session = self._session(500, text="<html>Internal error</html>")
        download = google_play.make_storage_downloader("pubsite_prod_rev_1", None, session=session)
        with self._quiet, self.assertRaises(RuntimeError) as ctx:
            download("stats/installs/x.csv")
        self.assertIn("500", str(ctx.exception))
        self.assertIn("Internal error", str(ctx.exception))
