#!/usr/bin/env python
# -*- coding: utf-8 -*-
# __author__ = 'Rohit Gupta'

"""
Cryptocurrency price History from coinmarketcap.com
"""

from __future__ import print_function

__all__ = ["CmcScraper"]

import os
import warnings

from .exporter import Exporter
from .parser import Parser
from .utils import InvalidParameters, download_coin_data


def _emit_id_number_info(coin_data, coin_code, coin_name):
    """Print the informational notes when an id_number mismatch is found."""
    show_coin_info = False
    if coin_code and coin_code.upper() != coin_data["data"]["symbol"].upper():
        print(
            f"INFO: Using 'id_number'! The 'coin_code' ({coin_code}) provided "
            + "is different from the symbol returned."
        )
        show_coin_info = True
    if coin_name and coin_name.lower() != coin_data["data"]["name"].lower():
        print(
            f"INFO: Using 'id_number'! The 'coin_name' ({coin_name}) provided "
            + "is different from the name returned."
        )
        show_coin_info = True
    if show_coin_info:
        print(
            f"""The returned data belongs to coin "{coin_data['data']['name']}", """
            + f"""with symbol "{coin_data['data']['symbol']}" """
        )


class CmcScraper(object):
    """
    Scrape cryptocurrency historical market price data from coinmarketcap.com

    The fetch/parse/export pipeline is injectable via ``fetcher``,
    ``parser`` and ``exporter``; the defaults reproduce the original
    behaviour byte-for-byte.
    """

    def __init__(
        self,
        coin_code=None,
        start_date=None,
        end_date=None,
        all_time=False,
        order_ascending=False,
        fiat="USD",
        coin_name=None,
        id_number=None,
        fetcher=None,
        parser=None,
        exporter=None,
    ):
        """
        :param coin_code: coin code of cryptocurrency e.g. btc. Will be ignored if using id_number.
        :param start_date: date since when to scrape data (in the format of dd-mm-yyyy)
        :param end_date: date to which scrape the data (in the format of dd-mm-yyyy).
            Defaults to today. CMC only returns completed daily candles, so the latest
            candle returned will always be the last completed UTC day.
        :param all_time: 'True' if need data of all time for respective cryptocurrency
        :param order_ascending: data ordered by 'Date' in ascending order (i.e. oldest first).
        :param fiat: fiat code eg. USD, EUR
        :param coin_name: coin name in case of many coins with same code e.g. sol -> solana, solcoin
        :param id_number: id number for the a cryptocurrency on the coinmarketcap.com.
            Will override coin_code and coin_name when provided.
        :param fetcher: (optional) injected :class:`~cryptocmd.fetcher.Fetcher`.
            When omitted the legacy ``download_coin_data`` pipeline is used.
        :param parser: (optional) injected :class:`~cryptocmd.parser.Parser`.
        :param exporter: (optional) injected :class:`~cryptocmd.exporter.Exporter`.
        """

        self.coin_code = coin_code
        self.start_date = start_date
        self.end_date = end_date
        self.all_time = bool(all_time)
        self.order_ascending = order_ascending
        self.fiat = fiat
        self.coin_name = coin_name
        self.fetcher = fetcher
        self.parser = parser if parser is not None else Parser()
        self.exporter = exporter if exporter is not None else Exporter()
        self.headers = self.parser.headers
        self.rows = []
        self.id_number = id_number

        # enable all_time download if start_time or end_time is not given
        if not (self.start_date and self.end_date):
            self.all_time = True

        if not (self.all_time or (self.start_date and self.end_date)):
            raise InvalidParameters(
                "'start_date' or 'end_date' cannot be empty if 'all_time' flag is False"
            )

    def __repr__(self):
        return (
            "<CmcScraper coin_code:{}, start_date:{}, end_date:{}, all_time:{}>".format(
                self.coin_code, self.start_date, self.end_date, self.all_time
            )
        )

    def _download_data(self, **kwargs):
        """
        This method downloads the data.
        :param forced: (optional) if ``True``, data will be re-downloaded.
        :return:
        """

        forced = kwargs.get("forced")

        if self.rows and not forced:
            return

        if self.all_time:
            self.start_date, self.end_date = None, None

        if self.fetcher is not None:
            coin_data = self.fetcher.fetch_history(
                self.coin_code,
                self.start_date,
                self.end_date,
                self.fiat,
                self.coin_name,
                self.id_number,
                parser=self.parser,
            )
            if self.id_number:
                _emit_id_number_info(
                    coin_data, self.coin_code, self.coin_name
                )
        else:
            coin_data = download_coin_data(
                self.coin_code,
                self.start_date,
                self.end_date,
                self.fiat,
                self.coin_name,
                self.id_number,
            )

        self.rows = self.parser.parse_rows(
            coin_data, order_ascending=self.order_ascending
        )

        if self.rows:
            self.end_date, self.start_date = self.rows[0][0], self.rows[-1][0]

    def get_data(self, format="", verbose=False, **kwargs):
        """
        This method returns the downloaded data in specified format.
        :param format: extension name of data format. Available: json, xls, yaml, csv, dbf, tsv, html, latex, xlsx, ods
        :param verbose: (optional) Flag to enable verbose only.
        :param kwargs: Optional arguments that data downloader takes.
        :return:
        """

        self._download_data(**kwargs)
        if verbose:
            print(*self.headers, sep=", ")

            for row in self.rows:
                print(*row, sep=", ")
        elif format:
            return self.exporter.export_format(format, self.headers, self.rows)
        else:
            return self.headers, self.rows

    @property
    def coins(self):
        """Downloaded table rows (plain ``list`` of ``list``)."""
        self._download_data()
        return self.rows

    def get_dataframe(self, date_as_index=False, **kwargs):
        """
        This gives scraped data as DataFrame.
        :param date_as_index: make 'Date' as index and remove 'Date' column.
        :param kwargs: Optional arguments that data downloader takes.
        :return: DataFrame of the downloaded data.
        """

        self._download_data(**kwargs)
        return self.exporter.to_pandas(
            self.headers, self.rows, date_as_index=date_as_index
        )

    def to_pandas(self, date_as_index=False, **kwargs):
        """Return the downloaded data as a pandas DataFrame.

        pandas is imported lazily; ``NotImplementedError`` is raised with the
        original message when it is not installed.
        """
        return self.get_dataframe(date_as_index=date_as_index, **kwargs)

    def to_csv(self, path=None, **kwargs):
        """Return the table as CSV text, or write it to ``path``.

        With no ``path`` the CSV text (identical to tablib's CSV export) is
        returned; with a ``path`` the UTF-8 bytes are written there and the
        path returned.  pandas is never required.
        """
        self._download_data(**kwargs)
        if path is None:
            return self.exporter.csv_text(self.headers, self.rows)
        return self.exporter.write_csv(path, self.headers, self.rows)

    def to_html(self, path=None, title="Crypto Market Data", **kwargs):
        """Return a standalone HTML page string, or write it to ``path``."""
        self._download_data(**kwargs)
        if path is None:
            return self.exporter.html_text(
                self.headers, self.rows, title=title
            )
        return self.exporter.write_html(
            path, self.headers, self.rows, title=title
        )

    def export_html(self, name=None, path=None, **kwargs):
        """Export the scraped data into a standalone HTML file."""
        self._download_data(**kwargs)

        if path is None:
            # Export in current directory if path not specified
            path = os.getcwd()

        if name is None:
            name = "{0}_{1}-{2}_{3}".format(
                self.coin_code, self.fiat, self.start_date, self.end_date
            )

        if not name.endswith(".html"):
            name += ".html"

        _file = "{0}/{1}".format(path, name)

        try:
            self.exporter.write_html(_file, self.headers, self.rows)
        except IOError as err:
            errno, strerror = err.args
            print("I/O error({0}): {1}".format(errno, strerror))
        return _file

    def export_csv(self, csv_name=None, csv_path=None, **kwargs):
        """
        This exports scraped data into a csv.
        :param csv_name: (optional) name of csv file.
        :param csv_path: (optional) path to where export csv file.
        :param kwargs: Optional arguments that data downloader takes.
        :return:
        """
        warnings.warn(
            "export_csv will be deprecated; Use 'export' method instead, e.g. export('csv')",
            PendingDeprecationWarning,
            stacklevel=2,
        )

        self._download_data(**kwargs)

        if csv_path is None:
            # Export in current directory if path not specified
            csv_path = os.getcwd()

        if csv_name is None:
            # Make name fo file in format of {coin_code}_{fiat}_{start_date}_{end_date}.csv
            csv_name = "{0}_{1}_{2}_{3}.csv".format(
                self.coin_code, self.fiat, self.start_date, self.end_date
            )

        if not csv_name.endswith(".csv"):
            csv_name += ".csv"

        _csv = "{0}/{1}".format(csv_path, csv_name)

        try:
            self.exporter.write_csv_legacy(_csv, self.headers, self.rows)
        except IOError as err:
            errno, strerror = err.args
            print("I/O error({0}): {1}".format(errno, strerror))

    def export(self, format, name=None, path=None, **kwargs):
        """
        Exports the data to specified file format
        :param format: extension name of file format. Available: json, xls, yaml, csv, dbf, tsv, html, latex, xlsx, ods
        :param name: (optional) name of file.
        :param path: (optional) output file path.
        :param kwargs: Optional arguments that data downloader takes.
        :return:
        """

        data = self.get_data(format, **kwargs)

        if path is None:
            # Export in current directory if path not specified
            path = os.getcwd()

        if name is None:
            # Make name of file in format: {coin_code}_{fiat}_{start_date}_{end_date}.csv
            name = "{0}_{1}-{2}_{3}".format(
                self.coin_code, self.fiat, self.start_date, self.end_date
            )

        if not name.endswith(".{}".format(format)):
            name += ".{}".format(format)

        _file = "{0}/{1}".format(path, name)

        try:
            if format == "csv":
                self.exporter.write_csv(_file, self.headers, self.rows)
            else:
                self.exporter.write_bytes_or_text(_file, data)
        except IOError as err:
            errno, strerror = err.args
            print("I/O error({0}): {1}".format(errno, strerror))
        except Exception as err:
            print("format: {0}, Error: {1}".format(format, err))
