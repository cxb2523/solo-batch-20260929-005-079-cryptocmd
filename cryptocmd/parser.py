#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Parser layer: converts CoinMarketCap JSON payloads into header/row tables.
"""

from datetime import datetime


class Parser(object):
    """Parses CoinMarketCap historical data into rows."""

    headers = [
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

    def parse(self, coin_data):
        """
        Convert a CMC historical data payload into table rows.
        :param coin_data: json data as returned by the fetcher.
        :return: list of rows, newest first.
        """
        rows = []
        for _row in coin_data["data"]["quotes"]:
            _row_quote = _row["quote"]
            date = datetime.strptime(
                _row["timeOpen"], "%Y-%m-%dT%H:%M:%S.%fZ"
            ).strftime("%d-%m-%Y")

            row = [
                date,
                _row_quote["open"],
                _row_quote["high"],
                _row_quote["low"],
                _row_quote["close"],
                _row_quote["volume"],
                _row_quote["marketCap"],
                _row["timeOpen"],
                _row["timeHigh"],
                _row["timeLow"],
                _row["timeClose"],
            ]

            rows.insert(0, row)
        return rows
