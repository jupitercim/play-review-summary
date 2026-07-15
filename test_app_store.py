"""app_store.py 的单元测试：JWT 构造 + customerReviews 解析（不打真实网络）。"""
import unittest
from datetime import datetime, timezone
from unittest import mock

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import app_store

SINCE = datetime(2026, 7, 8, tzinfo=timezone.utc)
IN_WINDOW_ISO = "2026-07-10T12:00:00Z"
OUT_OF_WINDOW_ISO = "2026-07-01T12:00:00Z"


def _generate_test_private_key_pem():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _apple_review(created_at_iso, rating, title, body, territory, nickname):
    return {
        "attributes": {
            "rating": rating,
            "title": title,
            "body": body,
            "reviewerNickname": nickname,
            "createdDate": created_at_iso,
            "territory": territory,
        }
    }


def _fake_response(payload):
    response = mock.Mock()
    response.raise_for_status = mock.Mock()
    response.json.return_value = payload
    return response


class BuildJwtTest(unittest.TestCase):
    def test_header_and_payload_fields(self):
        private_key_pem = _generate_test_private_key_pem()
        token = app_store.build_jwt("KEY123", "ISSUER456", private_key_pem)

        header = jwt.get_unverified_header(token)
        self.assertEqual(header["alg"], "ES256")
        self.assertEqual(header["kid"], "KEY123")
        self.assertEqual(header["typ"], "JWT")

        payload = jwt.decode(token, algorithms=["ES256"], options={"verify_signature": False})
        self.assertEqual(payload["iss"], "ISSUER456")
        self.assertEqual(payload["aud"], "appstoreconnect-v1")
        self.assertEqual(payload["exp"] - payload["iat"], 20 * 60)


class ExtractEntriesTest(unittest.TestCase):
    def test_keeps_reviews_within_window(self):
        reviews = [_apple_review(IN_WINDOW_ISO, 5, "Great", "Works well", "USA", "alice")]
        entries = app_store.extract_entries(reviews, since=SINCE)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["rating"], 5)
        self.assertEqual(entry["text"], "Great\nWorks well")
        self.assertEqual(entry["author"], "alice")
        self.assertEqual(entry["territory"], "USA")
        self.assertEqual(
            entry["modified_at"], datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        )

    def test_drops_reviews_before_window(self):
        reviews = [_apple_review(OUT_OF_WINDOW_ISO, 5, "Old", "review", "USA", "bob")]
        self.assertEqual(app_store.extract_entries(reviews, since=SINCE), [])

    def test_uses_body_only_when_title_missing(self):
        reviews = [_apple_review(IN_WINDOW_ISO, 4, "", "Just a body", "CHN", "carol")]
        entries = app_store.extract_entries(reviews, since=SINCE)
        self.assertEqual(entries[0]["text"], "Just a body")

    def test_missing_nickname_falls_back_to_anonymous(self):
        review = _apple_review(IN_WINDOW_ISO, 3, "Meh", "It's ok", "CHN", "placeholder")
        del review["attributes"]["reviewerNickname"]
        entries = app_store.extract_entries([review], since=SINCE)
        self.assertEqual(entries[0]["author"], "匿名用户")

    def test_sorted_newest_first(self):
        older = _apple_review(IN_WINDOW_ISO, 3, "older", "old", "USA", "dave")
        newer = _apple_review("2026-07-11T12:00:00Z", 5, "newer", "new", "USA", "erin")
        entries = app_store.extract_entries([older, newer], since=SINCE)
        self.assertEqual([e["text"] for e in entries], ["newer\nnew", "older\nold"])


class FetchReviewsPaginationTest(unittest.TestCase):
    def setUp(self):
        self.private_key_pem = _generate_test_private_key_pem()

    @mock.patch("app_store.requests.get")
    def test_stops_paginating_once_page_is_out_of_window(self, mock_get):
        page1 = _fake_response(
            {
                "data": [_apple_review(IN_WINDOW_ISO, 5, "Great", "Nice", "USA", "alice")],
                "links": {"next": "https://api.appstoreconnect.apple.com/v1/apps/123/customerReviews?cursor=abc"},
            }
        )
        page2 = _fake_response(
            {
                "data": [_apple_review(OUT_OF_WINDOW_ISO, 1, "Bad", "Meh", "USA", "bob")],
                "links": {},
            }
        )
        mock_get.side_effect = [page1, page2]

        reviews = app_store.fetch_reviews(
            "123", "KEY123", "ISSUER456", self.private_key_pem, since=SINCE,
        )

        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(len(reviews), 2)

    @mock.patch("app_store.requests.get")
    def test_stops_when_no_next_link(self, mock_get):
        page1 = _fake_response(
            {
                "data": [_apple_review(IN_WINDOW_ISO, 5, "Great", "Nice", "USA", "alice")],
                "links": {},
            }
        )
        mock_get.side_effect = [page1]

        reviews = app_store.fetch_reviews(
            "123", "KEY123", "ISSUER456", self.private_key_pem, since=SINCE,
        )

        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(len(reviews), 1)


if __name__ == "__main__":
    unittest.main()
