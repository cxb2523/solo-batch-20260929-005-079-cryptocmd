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
from datetime import datetime
from .utils import InvalidParameters
from .fetcher import Fetcher
from .parser import Parser
from .exporter import Exporter


class CmcScraper(object):
    """
    Scrape cryptocurrency historical market price data from coinmarketcap.com

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
        :param fetcher: (optional) Fetcher instance handling download/cache/retry.
        :param parser: (optional) Parser instance converting CMC payloads to rows.
        :param exporter: (optional) Exporter instance for csv/dataframe/html output.
        """

        self.coin_code = coin_code
        self.start_date = start_date
        self.end_date = end_date
        self.all_time = bool(all_time)
        self.order_ascending = order_ascending
        self.fiat = fiat
        self.coin_name = coin_name
        self._fetcher = fetcher if fetcher is not None else Fetcher()
        self._parser = parser if parser is not None else Parser()
        self._exporter = exporter if exporter is not None else Exporter()
        self.headers = list(self._parser.headers)
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

        coin_data = self._fetcher.fetch_coin_data(
            self.coin_code,
            self.start_date,
            self.end_date,
            self.fiat,
            self.coin_name,
            self.id_number,
        )

        self.rows = self._parser.parse(coin_data) + self.rows

        if self.rows:
            self.end_date, self.start_date = self.rows[0][0], self.rows[-1][0]

        if self.order_ascending:
            self.rows.sort(key=lambda x: datetime.strptime(x[0], "%d-%m-%Y"))

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
            return self._exporter.to_format(format, self.headers, self.rows)
        else:
            return self.headers, self.rows

    def get_dataframe(self, date_as_index=False, **kwargs):
        """
        This gives scraped data as DataFrame.
        :param date_as_index: make 'Date' as index and remove 'Date' column.
        :param kwargs: Optional arguments that data downloader takes.
        :return: DataFrame of the downloaded data.
        """

        # fail fast, before any download, when pandas is not installed
        self._exporter.require_pandas()

        self._download_data(**kwargs)

        return self._exporter.to_dataframe(
            self.headers, self.rows, date_as_index=date_as_index
        )

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
            self._exporter.to_csv_file(self.headers, self.rows, _csv)
        except IOError as err:
            errno, strerror = err.args
            print("I/O error({0}): {1}".format(errno, strerror))

    def export_html(self, name=None, path=None, **kwargs):
        """
        This exports scraped data into a standalone HTML page (inline styles,
        no external dependencies) that can be opened directly in a browser.
        :param name: (optional) name of the html file.
        :param path: (optional) path to where export the html file.
        :param kwargs: Optional arguments that data downloader takes.
        :return:
        """

        self._download_data(**kwargs)

        if path is None:
            # Export in current directory if path not specified
            path = os.getcwd()

        if name is None:
            # Make name fo file in format of {coin_code}_{fiat}_{start_date}_{end_date}.html
            name = "{0}_{1}_{2}_{3}.html".format(
                self.coin_code, self.fiat, self.start_date, self.end_date
            )

        if not name.endswith(".html"):
            name += ".html"

        _html = "{0}/{1}".format(path, name)

        title = "{0} {1} {2} - {3}".format(
            self.coin_code, self.fiat, self.start_date, self.end_date
        )

        try:
            self._exporter.to_html_file(self.headers, self.rows, _html, title=title)
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
            with open(_file, "wb") as f:
                if isinstance(data, str):
                    f.write(data.encode("utf-8"))
                else:
                    f.write(data)
        except IOError as err:
            errno, strerror = err.args
            print("I/O error({0}): {1}".format(errno, strerror))
        except Exception as err:
            print("format: {0}, Error: {1}".format(format, err))
