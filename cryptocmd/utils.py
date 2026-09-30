#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import datetime
import json
import os
import sys

from requests import get

from .fetcher import Fetcher, FiatRegistry


with open(os.path.join(os.path.dirname(__file__), "fiat_ids.json")) as _f:
    _FIAT_IDS = json.load(_f)

_SUPPORTED_FIATS = ", ".join(sorted(_FIAT_IDS))

_HEADERS = {"User-Agent": "Mozilla/5.0"}

_FIAT_REGISTRY = FiatRegistry(mapping=_FIAT_IDS)
_default_fetcher = None


def _get_default_fetcher():
    """Lazily build the process-wide Fetcher used by the legacy helpers.

    The transport is late-bound to this module's ``get_url_data`` so code
    (and tests) that patch ``cryptocmd.utils.get_url_data`` keep intercepting
    every request made by the legacy entry points.
    """
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = Fetcher(
            headers=_HEADERS, fiat_registry=_FIAT_REGISTRY
        )
    return _default_fetcher


def _legacy_transport(url):
    # Indirection so a runtime patch of ``get_url_data`` always takes effect.
    return get_url_data(url)


def get_url_data(url):
    """
    This method downloads the data of the web page.
    :param url: 'url' of the web page to download
    :return: response object of get request of the 'url'
    """

    try:
        response = get(url, headers=_HEADERS)
        return response
    except Exception as e:
        print("Error message (get_url_data) :", e)
        raise e


def get_coin_id(coin_code, coin_name):
    """
    This method fetches the id of currency from the given code
    :param coin_code: coin code of a cryptocurrency e.g. btc
    :param coin_name: coin name in case of many coins with same code e.g. sol -> solana, solcoin
    :return: numeric coin id for the cryptocurrency on coinmarketcap.com
    """

    try:
        return _get_default_fetcher().get_coin_id(
            coin_code, coin_name, transport=_legacy_transport
        )
    except Exception as e:
        print("Error fetching coin id data for coin code {}".format(coin_code))
        print("Error message:", e)
        raise e


def download_coin_data(
    coin_code, start_date, end_date, fiat, coin_name, id_number=None
):
    """
    Download price history for the specified cryptocurrency and time range from CoinMarketCap.

    :param coin_code: coin code of a cryptocurrency e.g. btc
    :param start_date: date since when to scrape data (in the format of dd-mm-yyyy)
    :param end_date: date to which scrape the data (in the format of dd-mm-yyyy).
        Defaults to today. CMC only returns completed daily candles, so the latest
        candle returned will always be the last completed UTC day regardless of this value.
    :param fiat: fiat code eg. USD, EUR
    :param coin_name: coin name in case of many coins with same code e.g. sol -> solana, solcoin
    :param id_number: id number for the token on coinmarketcap. Will override coin_code and coin_name when provided.

    :return: json data with historical OHLCV data for the cryptocurrency
    """

    if start_date is None:
        # default start date on coinmarketcap.com
        start_date = "28-4-2013"

    if end_date is None:
        end_date = datetime.date.today().strftime("%d-%m-%Y")

    coin_id = id_number if id_number else get_coin_id(coin_code, coin_name)
    convert_id = _FIAT_IDS.get(fiat.upper())
    if convert_id is None:
        raise ValueError(f"Unknown fiat '{fiat}'. Supported: {_SUPPORTED_FIATS}.")

    start_dt = datetime.datetime.strptime(start_date, "%d-%m-%Y").replace(
        tzinfo=datetime.timezone.utc
    )
    end_dt = datetime.datetime.strptime(end_date, "%d-%m-%Y").replace(
        tzinfo=datetime.timezone.utc
    )

    try:
        pages, result_json = _get_default_fetcher().fetch_pages(
            coin_id=coin_id,
            convert_id=convert_id,
            start_dt=start_dt,
            end_dt=end_dt,
            coin_code=coin_code,
            coin_name=coin_name,
            id_number=id_number,
            fiat=fiat,
            transport=_legacy_transport,
        )
        result_json["data"]["quotes"] = [
            quote
            for page in pages
            for quote in page["data"]["quotes"]
        ]

        if id_number:
            show_coin_info = False
            if coin_code and coin_code.upper() != result_json["data"]["symbol"].upper():
                print(
                    f"INFO: Using 'id_number'! The 'coin_code' ({coin_code}) provided "
                    + "is different from the symbol returned."
                )
                show_coin_info = True
            if coin_name and coin_name.lower() != result_json["data"]["name"].lower():
                print(
                    f"INFO: Using 'id_number'! The 'coin_name' ({coin_name}) provided "
                    + "is different from the name returned."
                )
                show_coin_info = True
            if show_coin_info:
                print(
                    f"""The returned data belongs to coin "{result_json['data']['name']}", """
                    + f"""with symbol "{result_json['data']['symbol']}" """
                )
        return result_json
    except Exception as e:
        print(
            "Error fetching price data for {} for interval '{}' and '{}'".format(
                f"(id {id_number})" if id_number else coin_code,
                start_date,
                end_date,
            )
        )
        print("Error message (download_data) :", e)
        raise e


def _replace(s, bad_chars):
    if sys.version_info > (3, 0):
        # For Python 3
        without_bad_chars = str.maketrans("", "", bad_chars)
        return s.translate(without_bad_chars)
    else:
        # For Python 2
        import string

        identity = string.maketrans("", "")
        return s.translate(identity, bad_chars)


class InvalidParameters(ValueError):
    """Passed parameters are invalid."""


class InvalidCoinCode(NotImplementedError):
    """This coin code is unavailable on 'coinmarketcap.com'"""
