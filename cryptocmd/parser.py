#!/usr/bin/env python

"""
Parsing layer for CoinMarketCap responses.

The :class:`Parser` turns raw API payloads (plain ``dict`` data as returned by
the fetching layer) into header/row tables of native Python values.  It never
performs I/O and never returns internal types.
"""

from datetime import datetime

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

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_DATE_FORMAT = "%d-%m-%Y"


class Parser:
    """Parse CoinMarketCap listing and historical API payloads."""

    def __init__(self, headers=None):
        self.headers = list(HEADERS if headers is None else headers)

    def check_status(self, json_data):
        """Raise on a non-zero CMC API ``status.error_code``.

        Returns the payload untouched on success so it can be chained.
        """
        status = json_data.get("status", {})
        error_code = status.get("error_code")
        if error_code is not None and str(error_code) != "0":
            raise Exception(status.get("error_message", "Unknown error"))
        return json_data

    def parse_coin_id(self, json_data, coin_code, coin_name):
        """Extract the numeric coin id from a cryptocurrency listing payload."""
        from .utils import InvalidCoinCode

        error_code = json_data["status"]["error_code"]
        if str(error_code) == "0":
            coins = json_data["data"]["cryptoCurrencyList"]
            matches = [
                coin
                for coin in coins
                if coin["symbol"].upper() == coin_code.upper()
                and (coin_name is None or coin["name"].lower() == coin_name.lower())
            ]
            if matches:
                return matches[0]["id"]
            raise InvalidCoinCode(
                f"'{coin_code}' coin code is unavailable on coinmarketcap.com"
            )
        if str(error_code) == "400":
            raise InvalidCoinCode(
                f"'{coin_code}' coin code is unavailable on coinmarketcap.com"
            )
        raise Exception(json_data["status"]["error_message"])

    def iter_quotes(self, json_data):
        """Yield quote dicts from a (possibly stitched) historical payload."""
        for quote in json_data["data"]["quotes"]:
            yield quote

    def parse_row(self, quote):
        """Turn one CMC quote dict into a plain table row (``list``)."""
        quote_data = quote["quote"]
        date = datetime.strptime(quote["timeOpen"], _TIME_FORMAT).strftime(_DATE_FORMAT)
        return [
            date,
            quote_data["open"],
            quote_data["high"],
            quote_data["low"],
            quote_data["close"],
            quote_data["volume"],
            quote_data["marketCap"],
            quote["timeOpen"],
            quote["timeHigh"],
            quote["timeLow"],
            quote["timeClose"],
        ]

    def parse_rows(self, json_data, order_ascending=False):
        """Parse all quotes into rows.

        CMC returns newest quotes first and the original scraper preserved
        that order (newest first); ``order_ascending`` flips it to oldest
        first.
        """
        rows = []
        for quote in self.iter_quotes(json_data):
            rows.insert(0, self.parse_row(quote))

        if order_ascending:
            rows.sort(key=lambda row: datetime.strptime(row[0], _DATE_FORMAT))
        return rows
