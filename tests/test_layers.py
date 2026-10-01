"""
Tests for the Fetcher / Parser / Exporter layers.

All tests use mocked HTTP responses and run without network access.
"""

import json
import os
import threading
from unittest.mock import MagicMock, patch

import pytest

from cryptocmd import CmcScraper, Exporter, Fetcher, Parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_quote(date_str):
    return {
        "timeOpen": f"{date_str}T00:00:00.000Z",
        "timeHigh": f"{date_str}T12:00:00.000Z",
        "timeLow": f"{date_str}T06:00:00.000Z",
        "timeClose": f"{date_str}T23:59:59.999Z",
        "quote": {
            "open": 50000.0,
            "high": 51000.0,
            "low": 49000.0,
            "close": 50500.0,
            "volume": 1000000.0,
            "marketCap": 900000000000.0,
        },
    }


def _make_response(quotes, coin_id=1, symbol="BTC", name="Bitcoin"):
    return {
        "status": {"error_code": 0, "error_message": None},
        "data": {"id": coin_id, "symbol": symbol, "name": name, "quotes": quotes},
    }


def _mock_response(response_dict):
    mock_response = MagicMock()
    mock_response.json.return_value = response_dict
    return mock_response


HEADERS = [
    "Date",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Market Cap",
    "Time Open",
    "Time High",
    "Time Low",
    "Time Close",
]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParser:
    def test_parse_returns_rows_newest_first(self):
        parser = Parser()
        data = _make_response([_make_quote("2026-04-24"), _make_quote("2026-04-25")])
        rows = parser.parse(data)
        assert [r[0] for r in rows] == ["25-04-2026", "24-04-2026"]
        assert rows[0][1] == 50000.0
        assert rows[0][7] == "2026-04-25T00:00:00.000Z"

    def test_headers_match_scraper_headers(self):
        assert Parser().headers == CmcScraper("BTC", id_number=1).headers


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------


class TestExporter:
    def _rows(self):
        return Parser().parse(_make_response([_make_quote("2026-04-25")]))

    def test_csv_file_bytes(self, tmp_path):
        path = str(tmp_path / "out.csv")
        Exporter().to_csv_file(HEADERS, self._rows(), path)
        with open(path, "rb") as f:
            content = f.read()
        assert content.startswith(b'"Date","Open","High","Low","Close"')
        assert b'"25-04-2026",50000.0,51000.0,49000.0,50500.0' in content

    def test_to_format_csv_matches_tablib(self):
        import tablib

        rows = self._rows()
        expected = tablib.Dataset()
        expected.headers = HEADERS
        for row in rows:
            expected.append(row)
        assert Exporter().to_format("csv", HEADERS, rows) == expected.export("csv")

    def test_to_dataframe(self):
        pd = pytest.importorskip("pandas")
        df = Exporter().to_dataframe(HEADERS, self._rows(), date_as_index=True)
        assert isinstance(df, pd.DataFrame)
        assert df.index.name == "Date"
        assert "Date" not in df.columns

    def test_to_dataframe_without_pandas_fails_before_download(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def no_pandas(name, *args, **kwargs):
            if name == "pandas":
                raise ImportError("No module named 'pandas'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_pandas)
        with patch("cryptocmd.utils.get_url_data") as mock_get:
            scraper = CmcScraper("BTC", "24-04-2026", "25-04-2026", id_number=1)
            with pytest.raises(NotImplementedError):
                scraper.get_dataframe()
        mock_get.assert_not_called()

    def test_html_is_standalone_page(self, tmp_path):
        path = str(tmp_path / "out.html")
        Exporter().to_html_file(HEADERS, self._rows(), path, title="BTC USD")
        with open(path, encoding="utf-8") as f:
            page = f.read()
        assert page.startswith("<!DOCTYPE html>")
        assert "<style>" in page
        assert "<title>BTC USD</title>" in page
        assert "<th>Market Cap</th>" in page
        assert "<td>25-04-2026</td>" in page
        assert page.rstrip().endswith("</html>")

    def test_html_escapes_values(self):
        page = Exporter().to_html(["<b>"], [["<script>"]])
        assert "<script>" not in page
        assert "&lt;script&gt;" in page

    def test_scraper_export_html(self, tmp_path):
        response = _make_response([_make_quote("2026-04-25")])
        with patch("cryptocmd.utils.get_url_data") as mock_get:
            mock_get.return_value = _mock_response(response)
            scraper = CmcScraper("BTC", "24-04-2026", "25-04-2026", id_number=1)
            scraper.export_html(path=str(tmp_path))
        files = os.listdir(str(tmp_path))
        assert files == ["BTC_USD_25-04-2026_25-04-2026.html"]


# ---------------------------------------------------------------------------
# Fetcher — retry
# ---------------------------------------------------------------------------


class TestFetcherRetry:
    def test_retries_failed_get(self):
        response = _make_response([_make_quote("2026-04-25")])
        calls = []

        def flaky_get(url, headers=None):
            calls.append(url)
            if len(calls) == 1:
                raise ConnectionError("boom")
            return _mock_response(response)

        fetcher = Fetcher(max_retries=2, retry_backoff=0)
        with patch("cryptocmd.utils.get_url_data", side_effect=flaky_get):
            data = fetcher.fetch_coin_data(
                "BTC", "24-04-2026", "25-04-2026", "USD", None, id_number=1
            )
        assert len(calls) == 2
        assert data["data"]["quotes"]

    def test_gives_up_after_max_retries(self):
        fetcher = Fetcher(max_retries=1, retry_backoff=0)
        with patch(
            "cryptocmd.utils.get_url_data", side_effect=ConnectionError("down")
        ):
            with pytest.raises(ConnectionError):
                fetcher.fetch_coin_data(
                    "BTC", "24-04-2026", "25-04-2026", "USD", None, id_number=1
                )

    def test_uses_injected_session(self):
        session = MagicMock()
        session.get.return_value = _mock_response(
            _make_response([_make_quote("2026-04-25")])
        )
        fetcher = Fetcher(session=session)
        data = fetcher.fetch_coin_data(
            "BTC", "24-04-2026", "25-04-2026", "USD", None, id_number=1
        )
        assert session.get.called
        assert data["data"]["symbol"] == "BTC"


# ---------------------------------------------------------------------------
# Fetcher — cache
# ---------------------------------------------------------------------------


class TestFetcherCache:
    def _fetch(self, fetcher, mock_get, **overrides):
        kwargs = dict(
            coin_code="BTC",
            start_date="24-04-2026",
            end_date="25-04-2026",
            fiat="USD",
            coin_name=None,
            id_number=1,
        )
        kwargs.update(overrides)
        with patch("cryptocmd.utils.get_url_data", mock_get):
            return fetcher.fetch_coin_data(**kwargs)

    def _mock_get(self, quotes=None):
        mock = MagicMock()
        mock.return_value = _mock_response(
            _make_response(quotes or [_make_quote("2026-04-25")])
        )
        return mock

    def test_second_call_served_from_cache(self, tmp_path):
        fetcher = Fetcher(cache_dir=str(tmp_path))
        mock_get = self._mock_get()
        first = self._fetch(fetcher, mock_get)
        assert mock_get.call_count == 1
        second = self._fetch(fetcher, self._mock_get())
        assert second == first
        assert len(os.listdir(str(tmp_path))) == 1

    def test_cache_key_covers_coin_fiat_and_range(self, tmp_path):
        fetcher = Fetcher(cache_dir=str(tmp_path))
        self._fetch(fetcher, self._mock_get())
        self._fetch(fetcher, self._mock_get(), fiat="EUR")
        self._fetch(fetcher, self._mock_get(), end_date="26-04-2026")
        self._fetch(fetcher, self._mock_get(), id_number=2)
        assert len(os.listdir(str(tmp_path))) == 4

    def test_failed_pagination_leaves_no_cache(self, tmp_path):
        fetcher = Fetcher(cache_dir=str(tmp_path), max_retries=0)
        # all-time range -> multiple chunks; fail on the second one
        calls = []

        def failing_get(url):
            calls.append(url)
            if len(calls) > 1:
                raise ConnectionError("mid-pagination failure")
            return _mock_response(_make_response([_make_quote("2020-01-01")]))

        with patch("cryptocmd.utils.get_url_data", side_effect=failing_get):
            with pytest.raises(ConnectionError):
                fetcher.fetch_coin_data(
                    "BTC", "01-01-2020", "01-01-2022", "USD", None, id_number=1
                )
        assert os.listdir(str(tmp_path)) == []

    def test_concurrent_fetchers_get_consistent_data(self, tmp_path):
        quotes = [_make_quote("2026-04-25")]
        mock_get = self._mock_get(quotes)
        results = {}

        def work(coin_id):
            fetcher = Fetcher(cache_dir=str(tmp_path))
            results[coin_id] = self._fetch(fetcher, mock_get, id_number=coin_id)

        threads = [threading.Thread(target=work, args=(i,)) for i in (1, 2, 3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert set(results) == {1, 2, 3}
        # one cache file per coin, each fully written and readable
        files = os.listdir(str(tmp_path))
        assert len(files) == 3
        for name in files:
            with open(os.path.join(str(tmp_path), name), encoding="utf-8") as f:
                cached = json.load(f)
            assert cached["data"]["quotes"] == quotes

    def test_corrupt_cache_file_is_ignored(self, tmp_path):
        fetcher = Fetcher(cache_dir=str(tmp_path))
        key = fetcher._cache_key("BTC", None, 1, "USD", "24-04-2026", "25-04-2026")
        with open(fetcher._cache_path(key), "w") as f:
            f.write("{not json")
        data = self._fetch(fetcher, self._mock_get())
        assert data["data"]["quotes"]
