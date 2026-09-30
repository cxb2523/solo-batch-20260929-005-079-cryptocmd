"""
Unit tests for the injectable Fetcher / Parser / Exporter layers.

These tests use in-memory fake HTTP transports, so they need no network.
"""

import csv
import datetime
import io
import os
import threading
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
import requests

from cryptocmd import CmcScraper, Exporter, Fetcher, FiatRegistry, Parser
from cryptocmd.utils import InvalidCoinCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def quote(day):
    return {
        "timeOpen": f"{day}T00:00:00.000Z",
        "timeHigh": f"{day}T12:00:00.000Z",
        "timeLow": f"{day}T06:00:00.000Z",
        "timeClose": f"{day}T23:59:59.999Z",
        "quote": {
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 9.0,
            "marketCap": 100.0,
        },
    }


def payload(quotes, symbol="BTC", name="Bitcoin", coin_id=1):
    return {
        "status": {"error_code": 0, "error_message": None},
        "data": {"id": coin_id, "symbol": symbol, "name": name,
                 "quotes": quotes},
    }


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class FakeSession:
    """requests.Session stand-in recording the URLs requested."""

    headers = {}

    def __init__(self, handler):
        self._handler = handler
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return self._handler(url)


LISTING = {
    "status": {"error_code": 0},
    "data": {"cryptoCurrencyList": [
        {"id": 1, "symbol": "BTC", "name": "Bitcoin"},
        {"id": 2, "symbol": "ETH", "name": "Ethereum"},
    ]},
}


def listing_response():
    return FakeResponse(LISTING)


def historical_handler(quotes_by_chunk):
    """Return a handler serving ``quotes_by_chunk[n]`` for the nth hist call."""

    state = {"n": 0}

    def handler(url):
        if "listing" in url:
            return listing_response()
        idx = state["n"]
        state["n"] += 1
        return FakeResponse(payload(quotes_by_chunk[idx]))

    return handler


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParser:
    def test_headers_default(self):
        assert Parser().headers == CmcScraper("BTC", id_number=1).headers

    def test_parse_rows_descending_by_default(self):
        data = payload([quote("2020-01-03"), quote("2020-01-02")])
        rows = Parser().parse_rows(data)
        assert [r[0] for r in rows] == ["02-01-2020", "03-01-2020"]

    def test_parse_rows_ascending(self):
        data = payload([quote("2020-01-03"), quote("2020-01-02")])
        rows = Parser().parse_rows(data, order_ascending=True)
        assert [r[0] for r in rows] == ["02-01-2020", "03-01-2020"]

    def test_parse_row_values(self):
        rows = Parser().parse_rows(payload([quote("2020-01-02")]))
        row = rows[0]
        assert row[0] == "02-01-2020"
        assert row[1:7] == [1.0, 2.0, 0.5, 1.5, 9.0, 100.0]
        assert row[7] == "2020-01-02T00:00:00.000Z"

    def test_check_status_raises(self):
        with pytest.raises(Exception):
            Parser().check_status(
                {"status": {"error_code": 5, "error_message": "bad"}}
            )

    def test_parse_coin_id_match_and_miss(self):
        parser = Parser()
        assert parser.parse_coin_id(LISTING, "btc", None) == 1
        assert parser.parse_coin_id(LISTING, "eth", "Ethereum") == 2
        with pytest.raises(InvalidCoinCode):
            parser.parse_coin_id(LISTING, "zzz", None)


# ---------------------------------------------------------------------------
# Fetcher: pagination and injection
# ---------------------------------------------------------------------------


class TestFetcherPagination:
    def test_stitches_yearly_chunks(self):
        chunks = [
            [quote("2019-06-01")],
            [quote("2020-06-01")],
            [quote("2021-06-01")],
        ]
        session = FakeSession(historical_handler(chunks))
        fetcher = Fetcher(session=session)
        data = fetcher.fetch_history(
            "BTC", "01-01-2019", "02-01-2022", "USD", None
        )
        historical = [u for u in session.urls if "historical" in u]
        assert len(historical) == 3
        assert len(data["data"]["quotes"]) == 3

    def test_coin_id_resolved_via_listing(self):
        session = FakeSession(historical_handler([[quote("2020-01-02")]]))
        fetcher = Fetcher(session=session)
        fetcher.fetch_history("BTC", "01-01-2020", "02-01-2020", "USD", None)
        assert any("listing" in u for u in session.urls)

    def test_unknown_fiat(self):
        fetcher = Fetcher(session=FakeSession(historical_handler([])))
        with pytest.raises(ValueError):
            fetcher.fetch_history(
                "BTC", "01-01-2020", "02-01-2020", "nope", None
            )

    def test_fiat_registry(self):
        reg = FiatRegistry(mapping={"USD": 2781, "EUR": 2790})
        assert reg.id_for("usd") == 2781
        assert reg.id_for("EUR") == 2790
        assert reg.id_for("XXX") is None
        assert "EUR" in reg.supported and "USD" in reg.supported

    def test_injected_scraper_returns_plain_data(self):
        chunks = [
            [quote("2020-01-02")],
            [quote("2021-01-02")],
            [quote("2022-01-02")],
        ]
        fetcher = Fetcher(session=FakeSession(historical_handler(chunks)))
        scraper = CmcScraper(
            "BTC", "01-01-2020", "02-01-2023",
            fetcher=fetcher, parser=Parser(), exporter=Exporter(),
        )
        headers, rows = scraper.get_data()
        assert isinstance(headers, list) and isinstance(rows, list)
        assert all(isinstance(r, list) for r in rows)
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# Fetcher: retries are GET-only / idempotency safe
# ---------------------------------------------------------------------------


class TestFetcherRetries:
    def test_retries_connection_error_then_succeeds(self):
        attempts = {"n": 0}

        def handler(url):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise requests.ConnectionError("boom")
            return FakeResponse(payload([quote("2020-01-02")]))

        session = FakeSession(handler)
        response = Fetcher(session=session, retries=2, backoff=0).get("u")
        assert attempts["n"] == 2 and response.status_code == 200

    def test_retries_503_but_not_404(self):
        seq = [
            FakeResponse({}, 503),
            FakeResponse({}, 503),
            FakeResponse(payload([quote("2020-01-02")]), 200),
        ]
        f = Fetcher(session=FakeSession(lambda u: seq.pop(0)),
                    retries=3, backoff=0)
        assert f.get("u").status_code == 200

        seen = {"n": 0}

        def h404(url):
            seen["n"] += 1
            return FakeResponse({}, 404)

        f = Fetcher(session=FakeSession(h404), retries=3, backoff=0)
        assert f.get("u").status_code == 404
        assert seen["n"] == 1

    def test_exhausted_retries_reraise(self):
        seen = {"n": 0}

        def handler(url):
            seen["n"] += 1
            raise requests.Timeout("slow")

        f = Fetcher(session=FakeSession(handler), retries=2, backoff=0)
        with pytest.raises(requests.Timeout):
            f.get("u")
        assert seen["n"] == 3

    def test_non_request_exception_is_not_retried(self):
        seen = {"n": 0}

        def handler(url):
            seen["n"] += 1
            raise ValueError("not a request error")

        f = Fetcher(session=FakeSession(handler), retries=3, backoff=0)
        with pytest.raises(ValueError):
            f.get("u")
        assert seen["n"] == 1


# ---------------------------------------------------------------------------
# Fetcher: cache
# ---------------------------------------------------------------------------


class TestFetcherCache:
    def _fetcher(self, tmp_path, handler=None):
        if handler is None:
            handler = historical_handler([[quote("2020-06-01")]])
        session = FakeSession(handler)
        return Fetcher(session=session, cache_dir=str(tmp_path)), session

    def test_cache_hit_makes_no_calls(self, tmp_path):
        fetcher, session = self._fetcher(tmp_path)
        fetcher.fetch_history("BTC", "01-01-2020", "02-01-2020", "USD", None)
        first = len(session.urls)
        assert first > 0
        fetcher.fetch_history("BTC", "01-01-2020", "02-01-2020", "USD", None)
        assert len(session.urls) == first

    def test_cache_key_covers_fiat_coin_and_range(self, tmp_path):
        def handler(url):
            if "listing" in url:
                return listing_response()
            return FakeResponse(payload([quote("2020-01-02")]))

        fetcher = Fetcher(session=FakeSession(handler), cache_dir=str(tmp_path))
        fetcher.fetch_history("BTC", "01-01-2020", "02-01-2020", "USD", None)
        fetcher.fetch_history("BTC", "01-01-2020", "02-01-2020", "EUR", None)
        fetcher.fetch_history("ETH", "01-01-2020", "02-01-2020", "USD", None)
        fetcher.fetch_history("BTC", "03-01-2020", "04-01-2020", "USD", None)
        files = [f for f in os.listdir(str(tmp_path)) if f.endswith(".json")]
        assert len(files) == 4

    def test_failure_mid_pagination_leaves_no_cache(self, tmp_path):
        state = {"n": 0}

        def handler(url):
            if "listing" in url:
                return listing_response()
            state["n"] += 1
            if state["n"] == 2:
                return FakeResponse(
                    {"status": {"error_code": 1, "error_message": "boom"}}
                )
            return FakeResponse(payload([quote("2020-01-02")]))

        fetcher = Fetcher(session=FakeSession(handler), cache_dir=str(tmp_path))
        with pytest.raises(Exception):
            fetcher.fetch_history(
                "BTC", "01-01-2019", "02-01-2022", "USD", None
            )
        assert os.listdir(str(tmp_path)) == []

    def test_cache_files_are_complete_no_tmp_left(self, tmp_path):
        fetcher, _ = self._fetcher(tmp_path)
        fetcher.fetch_history("BTC", "01-01-2020", "02-01-2020", "USD", None)
        leftovers = [f for f in os.listdir(str(tmp_path))
                     if f.endswith(".tmp")]
        assert leftovers == []

    def test_concurrent_coins_atomic_cache(self, tmp_path):
        def handler(url):
            if "listing" in url:
                return listing_response()
            cid = int(url.split("id=")[1].split("&")[0])
            return FakeResponse(
                payload([quote("2020-03-02")], symbol=f"C{cid}",
                        name=f"Coin{cid}", coin_id=cid)
            )

        fetcher = Fetcher(session=FakeSession(handler), cache_dir=str(tmp_path))
        errors = []

        def worker(i):
            try:
                for _ in range(3):
                    scraper = CmcScraper(
                        f"C{i}", "01-03-2020", "02-03-2020",
                        id_number=100 + i, fetcher=fetcher,
                    )
                    _, rows = scraper.get_data()
                    assert len(rows) == 1
                    assert rows[0][0] == "02-03-2020"
            except Exception as exc:  # pragma: no cover - failure reporting
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors
        files = [f for f in os.listdir(str(tmp_path)) if f.endswith(".json")]
        assert len(files) == 8
        assert not [f for f in os.listdir(str(tmp_path))
                    if f.endswith(".tmp")]


# ---------------------------------------------------------------------------
# Exporter: CSV parity, lazy pandas, HTML page
# ---------------------------------------------------------------------------


HEADERS = ["Date", "Open", "High", "Low", "Close", "Volume", "Market Cap"]
ROWS = [["02-01-2020", 1.0, 2.0, 0.5, 1.5, 9.0, 100.0]]


class TestExporter:
    def test_csv_text_matches_tablib(self):
        import tablib

        dataset = tablib.Dataset()
        dataset.headers = HEADERS
        dataset.append(ROWS[0])
        assert Exporter().csv_text(HEADERS, ROWS) == dataset.export("csv")

    def test_csv_uses_crlf_and_header(self):
        text = Exporter().csv_text(HEADERS, ROWS)
        assert text.split("\r\n")[0] == ",".join(HEADERS)
        assert text.endswith("\r\n")

    def test_write_csv_roundtrip(self, tmp_path):
        path = str(tmp_path / "out.csv")
        Exporter().write_csv(path, HEADERS, ROWS)
        with open(path, "rb") as fp:
            raw = fp.read()
        assert raw == Exporter().csv_text(HEADERS, ROWS).encode("utf-8")

    def test_to_pandas_dataframe(self):
        df = Exporter().to_pandas(HEADERS, ROWS)
        assert df.columns.tolist() == HEADERS
        assert str(df["Date"].dtype).startswith("datetime64")

    def test_to_pandas_date_index(self):
        df = Exporter().to_pandas(HEADERS, ROWS, date_as_index=True)
        assert df.index.name == "Date"
        assert "Date" not in df.columns

    def test_html_is_standalone_page(self):
        text = Exporter().html_text(HEADERS, ROWS)
        assert text.lstrip().startswith("<!DOCTYPE html>")
        assert "<html" in text and "</html>" in text
        assert "<table>" in text and "<th>Date</th>" in text
        assert "02-01-2020" in text

    def test_html_escapes_content(self):
        rows = [["<x>", 1, 2, 3, 4, 5, 6]]
        text = Exporter().html_text(HEADERS, rows)
        assert "&lt;x&gt;" in text

    def test_write_html_file(self, tmp_path):
        path = str(tmp_path / "page.html")
        Exporter().write_html(path, HEADERS, ROWS)
        with open(path, "rb") as fp:
            assert fp.read().decode("utf-8") == Exporter().html_text(
                HEADERS, ROWS
            )


# ---------------------------------------------------------------------------
# CmcScraper convenience surface (to_csv / to_pandas / to_html / coins)
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_scraper():
    with patch(
        "cryptocmd.utils.get_url_data",
        return_value=FakeResponse(payload([quote("2020-01-02")])),
    ):
        yield CmcScraper("BTC", "01-01-2020", "02-01-2020", id_number=1)


class TestScraperConvenience:
    def test_to_csv_returns_text(self, patched_scraper):
        text = patched_scraper.to_csv()
        assert isinstance(text, str)
        assert text.startswith(",".join(patched_scraper.headers))

    def test_to_csv_writes_path(self, patched_scraper, tmp_path):
        path = str(tmp_path / "x.csv")
        assert patched_scraper.to_csv(path) == path
        with open(path, "rb") as fp:
            assert fp.read().decode("utf-8") == patched_scraper.to_csv()

    def test_to_pandas(self, patched_scraper):
        df = patched_scraper.to_pandas()
        assert df.columns.tolist() == patched_scraper.headers

    def test_coins_property(self, patched_scraper):
        assert patched_scraper.coins is patched_scraper.rows
        assert patched_scraper.coins[0][0] == "02-01-2020"

    def test_to_html_string_and_file(self, patched_scraper, tmp_path):
        text = patched_scraper.to_html()
        assert text.lstrip().startswith("<!DOCTYPE html>")
        path = str(tmp_path / "btc.html")
        returned = patched_scraper.to_html(path)
        assert returned == path
        with open(path, encoding="utf-8") as fp:
            assert fp.read() == text

    def test_export_html_default_naming(self, patched_scraper, tmp_path,
                                        monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        path = patched_scraper.export_html(name="btc_page")
        assert os.path.isfile(path) and path.endswith("btc_page.html")
