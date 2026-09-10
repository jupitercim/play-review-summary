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

    def test_returns_none_when_no_data_in_either_month(self):
        download = self._downloader({})
        result = google_play.fetch_installs(
            "com.example.app", date(2026, 9, 8), downloader=download
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
