#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Fetcher layer: owns the HTTP session, retry policy, fiat id mapping,
historical pagination and the on-disk cache.
"""

from __future__ import print_function

import datetime
import hashlib
import json
import os
import tempfile
import threading
import time

from . import utils


class Fetcher(object):
    """
    Downloads cryptocurrency data from coinmarketcap.com.

    :param session: (optional) a ``requests.Session``-like object used for
        HTTP GETs. When omitted, requests go through
        :func:`cryptocmd.utils.get_url_data`.
    :param fiat_ids: (optional) mapping of fiat code -> coinmarketcap
        convert id. Defaults to the bundled ``fiat_ids.json``.
    :param cache_dir: (optional) directory for the on-disk JSON cache.
        ``None`` (default) disables caching.
    :param max_retries: number of retries for failed idempotent GETs.
    :param retry_backoff: base delay (seconds) between retries, scaled
        linearly with the attempt number.
    :param timeout: (optional) request timeout, only used with ``session``.
    """

    def __init__(
        self,
        session=None,
        fiat_ids=None,
        cache_dir=None,
        max_retries=2,
        retry_backoff=0.5,
        timeout=None,
    ):
        self.session = session
        self.fiat_ids = utils._FIAT_IDS if fiat_ids is None else fiat_ids
        self.cache_dir = cache_dir
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.timeout = timeout
        self._cache_locks = {}
        self._cache_locks_guard = threading.Lock()

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _get_json(self, url):
        """
        Perform an idempotent GET request and return the decoded JSON body.
        Failures are retried up to ``max_retries`` times.
        """
        attempt = 0
        while True:
            try:
                if self.session is not None:
                    response = self.session.get(
                        url, headers=utils._HEADERS, timeout=self.timeout
                    )
                else:
                    response = utils.get_url_data(url)
                return response.json()
            except Exception:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                time.sleep(self.retry_backoff * attempt)

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _cache_key(self, coin_code, coin_name, id_number, fiat, start_date, end_date):
        if id_number:
            identity = "id:{}".format(id_number)
        else:
            identity = "coin:{}:{}".format(
                (coin_code or "").upper(), (coin_name or "").lower()
            )
        return "|".join(
            [identity, "fiat:{}".format(fiat.upper()), start_date, end_date]
        )

    def _cache_path(self, key):
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, "{}.json".format(digest))

    def _cache_lock(self, key):
        with self._cache_locks_guard:
            return self._cache_locks.setdefault(key, threading.Lock())

    @staticmethod
    def _read_cache(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @staticmethod
    def _write_cache_atomic(path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(path), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_coin_id(self, coin_code, coin_name):
        """
        Fetch the id of currency from the given code.
        :param coin_code: coin code of a cryptocurrency e.g. btc
        :param coin_name: coin name in case of many coins with same code
        :return: numeric coin id for the cryptocurrency on coinmarketcap.com
        """

        api_url = (
            "{}/cryptocurrency/listing?start=1&limit=5000"
            "&sortBy=market_cap&sortType=desc&convert=USD"
            "&cryptoType=all&tagType=all&audited=false"
        ).format(utils._CMC_DATA_API)

        try:
            json_data = self._get_json(api_url)
            error_code = json_data["status"]["error_code"]
            if str(error_code) == "0":
                coins = json_data["data"]["cryptoCurrencyList"]
                matches = [
                    c
                    for c in coins
                    if c["symbol"].upper() == coin_code.upper()
                    and (coin_name is None or c["name"].lower() == coin_name.lower())
                ]
                if matches:
                    return matches[0]["id"]
                raise utils.InvalidCoinCode(
                    "'{}' coin code is unavailable on coinmarketcap.com".format(
                        coin_code
                    )
                )
            if str(error_code) == "400":
                raise utils.InvalidCoinCode(
                    "'{}' coin code is unavailable on coinmarketcap.com".format(
                        coin_code
                    )
                )
            raise Exception(json_data["status"]["error_message"])
        except Exception as e:
            print("Error fetching coin id data for coin code {}".format(coin_code))
            print("Error message:", e)
            raise e

    def fetch_coin_data(
        self, coin_code, start_date, end_date, fiat, coin_name, id_number=None
    ):
        """
        Fetch price history for the specified cryptocurrency and time range,
        using the on-disk cache when ``cache_dir`` is set. The cache key
        includes the coin, fiat and date range; a failed download never
        touches the cache.
        """

        if start_date is None:
            # default start date on coinmarketcap.com
            start_date = "28-4-2013"

        if end_date is None:
            end_date = datetime.date.today().strftime("%d-%m-%Y")

        if self.cache_dir is None:
            return self._download(
                coin_code, start_date, end_date, fiat, coin_name, id_number
            )

        key = self._cache_key(
            coin_code, coin_name, id_number, fiat, start_date, end_date
        )
        path = self._cache_path(key)

        cached = self._read_cache(path)
        if cached is not None:
            return cached

        # Serialize concurrent fetches of the same key within this process;
        # the atomic replace in _write_cache_atomic guarantees readers never
        # observe a partially written cache file.
        with self._cache_lock(key):
            cached = self._read_cache(path)
            if cached is not None:
                return cached
            data = self._download(
                coin_code, start_date, end_date, fiat, coin_name, id_number
            )
            self._write_cache_atomic(path, data)
            return data

    def _download(
        self, coin_code, start_date, end_date, fiat, coin_name, id_number=None
    ):
        """
        Download price history for the specified cryptocurrency and time range
        from CoinMarketCap, stitching together paginated (yearly) chunks.
        """

        coin_id = id_number if id_number else self.get_coin_id(coin_code, coin_name)
        convert_id = self.fiat_ids.get(fiat.upper())
        if convert_id is None:
            raise ValueError(
                "Unknown fiat '{}'. Supported: {}.".format(
                    fiat, ", ".join(sorted(self.fiat_ids))
                )
            )

        start_dt = datetime.datetime.strptime(start_date, "%d-%m-%Y").replace(
            tzinfo=datetime.timezone.utc
        )
        end_dt = datetime.datetime.strptime(end_date, "%d-%m-%Y").replace(
            tzinfo=datetime.timezone.utc
        )

        try:
            # CMC caps responses at ~365 entries per request; split into yearly chunks
            # so long/all-time ranges always return daily granularity.
            # timeStart is exclusive (first returned day = timeStart + 1), so each
            # chunk's timeStart equals the previous chunk's timeEnd — no overlap, no gap.
            all_quotes = []
            result_json = None
            chunk_start = start_dt - datetime.timedelta(days=1)

            while chunk_start < end_dt:
                chunk_end = min(chunk_start + datetime.timedelta(days=365), end_dt)
                api_url = (
                    "{}/cryptocurrency/historical"
                    "?id={}&convertId={}&timeStart={}&timeEnd={}"
                ).format(
                    utils._CMC_DATA_API,
                    coin_id,
                    convert_id,
                    int(chunk_start.timestamp()),
                    int(chunk_end.timestamp()),
                )
                json_data = self._get_json(api_url)
                status = json_data.get("status", {})
                error_code = status.get("error_code")
                if error_code and str(error_code) != "0":
                    raise Exception(status.get("error_message", "Unknown error"))
                if result_json is None:
                    result_json = json_data
                all_quotes.extend(json_data["data"]["quotes"])
                chunk_start = chunk_end + datetime.timedelta(days=1)

            result_json["data"]["quotes"] = all_quotes

            if id_number:
                show_coin_info = False
                if (
                    coin_code
                    and coin_code.upper() != result_json["data"]["symbol"].upper()
                ):
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
