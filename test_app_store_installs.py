"""app_store.py 每日销售报告（下载量）部分的单元测试（不打真实网络）。"""
import gzip
import unittest
from datetime import date
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import app_store

REPORT_DATE = date(2026, 9, 8)


def _generate_test_private_key_pem():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def make_sales_row(product_type, units, apple_identifier="123456789"):
    return {
        "Provider": "APPLE",
        "SKU": "com.example.app",
        "Title": "Example",
        "Product Type Identifier": product_type,
        "Units": str(units),
        "Apple Identifier": apple_identifier,
        "Begin Date": "09/08/2026",
        "Country Code": "US",
    }


def make_sales_tsv_gzip(rows):
    columns = list(rows[0].keys()) if rows else ["Provider", "Units"]
    lines = ["\t".join(columns)]
    for row in rows:
        lines.append("\t".join(row[c] for c in columns))
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


def _gzip_response(rows):
    response = mock.Mock()
    response.status_code = 200
    response.content = make_sales_tsv_gzip(rows)
    return response


def _error_response(status_code, detail):
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = {"errors": [{"status": str(status_code), "detail": detail}]}
    response.text = detail
    return response


class SummarizeDownloadsTest(unittest.TestCase):
    def test_groups_units_by_product_type_for_the_app(self):
        rows = [
            make_sales_row("1F", 40),
            make_sales_row("1T", 5),
            make_sales_row("3F", 12),
            make_sales_row("7F", 300),
            make_sales_row("IA1", 9),
        ]
        self.assertEqual(
            app_store.summarize_downloads(rows, "123456789"),
            {"first_downloads": 45, "redownloads": 12, "updates": 300},
        )

    def test_ignores_rows_for_other_apps(self):
        rows = [make_sales_row("1F", 40), make_sales_row("1F", 99, apple_identifier="999")]
        self.assertEqual(
            app_store.summarize_downloads(rows, "123456789")["first_downloads"], 40
        )

    def test_empty_rows_give_zero_counts(self):
        self.assertEqual(
            app_store.summarize_downloads([], "123456789"),
            {"first_downloads": 0, "redownloads": 0, "updates": 0},
        )


class FetchDailySalesRowsTest(unittest.TestCase):
    def setUp(self):
        self.private_key_pem = _generate_test_private_key_pem()

    def _fetch(self):
        return app_store.fetch_daily_sales_rows(
            "80012345", REPORT_DATE, "KEY123", "ISSUER456", self.private_key_pem
        )

    @mock.patch("app_store.requests.get")
    def test_decompresses_gzip_tsv_into_rows(self, mock_get):
        mock_get.return_value = _gzip_response([make_sales_row("1F", 40), make_sales_row("7F", 300)])

        rows = self._fetch()

        self.assertEqual([r["Product Type Identifier"] for r in rows], ["1F", "7F"])
        self.assertEqual(rows[0]["Units"], "40")
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["filter[frequency]"], "DAILY")
        self.assertEqual(params["filter[reportDate]"], "2026-09-08")
        self.assertEqual(params["filter[reportType]"], "SALES")
        self.assertEqual(params["filter[reportSubType]"], "SUMMARY")
        self.assertEqual(params["filter[vendorNumber]"], "80012345")
        self.assertEqual(params["filter[version]"], "1_1")

    @mock.patch("app_store.requests.get")
    def test_no_sales_404_means_zero_rows(self, mock_get):
        mock_get.return_value = _error_response(404, "There were no sales for the date specified.")
        self.assertEqual(self._fetch(), [])

    @mock.patch("app_store.requests.get")
    def test_retries_with_version_apple_says_is_latest(self, mock_get):
        mock_get.side_effect = [
            _error_response(
                400,
                "The version parameter you have specified is invalid. "
                "The latest version for this report is 1_2.",
            ),
            _gzip_response([make_sales_row("1F", 40)]),
        ]

        rows = self._fetch()

        self.assertEqual(len(rows), 1)
        self.assertEqual(mock_get.call_count, 2)
        second_params = mock_get.call_args_list[1].kwargs["params"]
        self.assertEqual(second_params["filter[version]"], "1_2")

    @mock.patch("app_store.requests.get")
    def test_other_404_is_reported_as_error_with_apple_detail(self, mock_get):
        mock_get.return_value = _error_response(404, "Report is not available yet.")
        with self.assertRaises(RuntimeError) as ctx:
            self._fetch()
        self.assertIn("Report is not available yet.", str(ctx.exception))

    @mock.patch("app_store.requests.get")
    def test_forbidden_is_reported_as_error(self, mock_get):
        mock_get.return_value = _error_response(403, "The API key in use does not allow this request")
        with self.assertRaises(RuntimeError) as ctx:
            self._fetch()
        self.assertIn("403", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
