"""google_play.py 商店表现（store_performance）报表部分的单元测试（不依赖网络）。"""
import unittest
from datetime import date

import google_play


def make_store_performance_csv(rows):
    """按 Play Console 导出格式构造 UTF-16 的 store_performance country CSV。
    rows: (date, country, acquisitions, visitors)"""
    header = (
        "Date,Package name,Country / region,Store listing acquisitions,"
        "Store listing visitors,Store listing conversion rate"
    )
    lines = [header]
    for day, country, acquisitions, visitors in rows:
        lines.append("{},com.example.app,{},{},{},0.4".format(day, country, acquisitions, visitors))
    return ("\r\n".join(lines) + "\r\n").encode("utf-16")


class StorePerformanceReportObjectTest(unittest.TestCase):
    def test_builds_country_object_name_for_month(self):
        name = google_play.store_performance_report_object("com.example.app", date(2026, 9, 8))
        self.assertEqual(
            name,
            "stats/store_performance/store_performance_com.example.app_202609_country.csv",
        )


class ParseStorePerformanceCsvTest(unittest.TestCase):
    def test_sums_country_rows_per_date(self):
        raw = make_store_performance_csv([
            ("2026-09-04", "ID", 20, 50),
            ("2026-09-04", "Other", 78, 178),
            ("2026-09-03", "Other", 98, 240),
        ])
        rows = google_play.parse_store_performance_csv(raw)
        self.assertEqual(sorted(rows), [date(2026, 9, 3), date(2026, 9, 4)])
        self.assertEqual(rows[date(2026, 9, 4)], {"acquisitions": 98, "visitors": 228})
        self.assertEqual(rows[date(2026, 9, 3)], {"acquisitions": 98, "visitors": 240})

    def test_blank_cells_count_as_zero(self):
        raw = make_store_performance_csv([("2026-09-04", "Other", "", "")])
        rows = google_play.parse_store_performance_csv(raw)
        self.assertEqual(rows[date(2026, 9, 4)], {"acquisitions": 0, "visitors": 0})


class FetchStorePerformanceTest(unittest.TestCase):
    SEP = "stats/store_performance/store_performance_com.example.app_202609_country.csv"
    AUG = "stats/store_performance/store_performance_com.example.app_202608_country.csv"

    def _downloader(self, files):
        calls = []

        def download(object_name):
            calls.append(object_name)
            return files.get(object_name)

        download.calls = calls
        return download

    def test_returns_target_date_totals_when_present(self):
        download = self._downloader({
            self.SEP: make_store_performance_csv([("2026-09-08", "ID", 20, 50), ("2026-09-08", "Other", 78, 178)])
        })
        result = google_play.fetch_store_performance("com.example.app", date(2026, 9, 8), downloader=download)
        self.assertEqual(result, {"date": date(2026, 9, 8), "acquisitions": 98, "visitors": 228})
        self.assertEqual(download.calls, [self.SEP])

    def test_falls_back_to_latest_earlier_date(self):
        download = self._downloader({
            self.SEP: make_store_performance_csv([("2026-09-03", "Other", 98, 240), ("2026-09-04", "Other", 90, 200)])
        })
        result = google_play.fetch_store_performance("com.example.app", date(2026, 9, 8), downloader=download)
        self.assertEqual(result["date"], date(2026, 9, 4))
        self.assertEqual(result["acquisitions"], 90)

    def test_looks_into_previous_month_at_month_start(self):
        download = self._downloader({
            self.AUG: make_store_performance_csv([("2026-08-31", "Other", 98, 240)])
        })
        result = google_play.fetch_store_performance("com.example.app", date(2026, 9, 1), downloader=download)
        self.assertEqual(result["date"], date(2026, 8, 31))
        self.assertEqual(download.calls, [self.SEP, self.AUG])

    def test_raises_when_no_report_file_exists(self):
        download = self._downloader({})
        with self.assertRaises(google_play.ReportNotFound) as ctx:
            google_play.fetch_store_performance("com.example.app", date(2026, 9, 8), downloader=download)
        self.assertIn("store_performance_com.example.app_202609_country.csv", str(ctx.exception))
        self.assertIn("store_performance_com.example.app_202608_country.csv", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
