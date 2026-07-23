import unittest
from datetime import date
from urllib.parse import parse_qs, urlparse

from oura_export import (
    build_authorization_url,
    endpoint_params,
    resolve_period,
)


class OuraExportTests(unittest.TestCase):
    def test_authorization_url_omits_blank_scope(self):
        url = build_authorization_url(
            client_id="client",
            redirect_uri="http://localhost:8000/callback",
            state="state-value",
        )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["client_id"], ["client"])
        self.assertEqual(query["state"], ["state-value"])
        self.assertNotIn("scope", query)

    def test_authorization_url_accepts_explicit_scopes(self):
        url = build_authorization_url(
            client_id="client",
            redirect_uri="http://localhost:8000/callback",
            state="state-value",
            scopes="daily heartrate",
        )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["scope"], ["daily heartrate"])

    def test_explicit_period(self):
        start, end = resolve_period(
            days=30,
            start=date(2026, 7, 1),
            end=date(2026, 7, 23),
        )
        self.assertEqual(start.isoformat(), "2026-07-01")
        self.assertEqual(end.isoformat(), "2026-07-23")

    def test_invalid_period(self):
        with self.assertRaises(ValueError):
            resolve_period(
                days=30,
                start=date(2026, 7, 23),
                end=date(2026, 7, 1),
            )

    def test_date_and_datetime_params(self):
        start = date(2026, 7, 1)
        end = date(2026, 7, 23)
        self.assertEqual(
            endpoint_params("date", start, end),
            {"start_date": "2026-07-01", "end_date": "2026-07-23"},
        )
        self.assertEqual(
            endpoint_params("datetime", start, end),
            {
                "start_datetime": "2026-07-01T00:00:00Z",
                "end_datetime": "2026-07-23T23:59:59Z",
            },
        )


if __name__ == "__main__":
    unittest.main()

