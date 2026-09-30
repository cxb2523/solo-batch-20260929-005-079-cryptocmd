#!/usr/bin/env python

"""
HTTP fetching layer for CoinMarketCap data.

The :class:`Fetcher` owns the ``requests`` session, idempotent-GET retries,
historical pagination and the on-disk cache.  Everything that leaves this
module is plain ``dict``/``list`` data -- never requests or internal types.
"""

import copy
import datetime
import hashlib
import json
import os
import threading
import time

import requests

CMC_DATA_API = "https://api.coinmarketcap.com/data-api/v3"

DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0"}

# CMC caps responses at ~365 entries per request; yearly chunks keep daily
# granularity for long/all-time ranges.
CHUNK_DAYS = 365

# Status codes worth retrying: rate limiting and transient server errors.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class FiatRegistry:
    """Mapping of fiat currency codes to CMC numeric convert ids."""

    def __init__(self, path=None, mapping=None):
        if mapping is not None:
            self._ids = dict(mapping)
        else:
            if path is None:
                path = os.path.join(os.path.dirname(__file__), "fiat_ids.json")
            with open(path) as fp:
                self._ids = json.load(fp)

    @property
    def supported(self):
        """Human readable, sorted list of supported fiat codes."""
        return ", ".join(sorted(self._ids))

    def id_for(self, fiat):
        """Return the numeric convert id for ``fiat`` or ``None``."""
        return self._ids.get(fiat.upper())


class Fetcher:
    """Fetch CoinMarketCap listing and historical data.

    :param session:        a ``requests.Session``-like object (a fresh
                           ``requests.Session`` is created when omitted)
    :param headers:        extra request headers merged onto the session
    :param retries:        number of *additional* attempts for idempotent GET
                           requests (``0`` disables retries and preserves the
                           original single-shot behaviour)
    :param backoff:        base seconds between retries; attempt ``n`` sleeps
                           ``backoff * 2 ** (n - 1)``
    :param cache_dir:      when set, fully assembled historical responses are
                           cached here.  Cache files are written atomically
                           (temp file + ``os.replace``) and only after every
                           page of a range has been fetched successfully.
    :param fiat_registry:  :class:`FiatRegistry` providing fiat code -> id
    """

    def __init__(
        self,
        session=None,
        headers=None,
        retries=0,
        backoff=0.5,
        cache_dir=None,
        fiat_registry=None,
    ):
        self.session = session if session is not None else requests.Session()
        if headers is None:
            headers = DEFAULT_HEADERS
        self.session.headers.update(headers)
        self.retries = max(0, int(retries))
        self.backoff = backoff
        self.cache_dir = cache_dir
        self.fiat = fiat_registry if fiat_registry is not None else FiatRegistry()
        # One lock per cache key so different coins never block each other.
        self._cache_locks = {}
        self._cache_locks_guard = threading.Lock()

    # ------------------------------------------------------------------
    # Low level GET -- the only method that performs network I/O.
    # ------------------------------------------------------------------

    def get(self, url, transport=None):
        """Perform an idempotent GET, retrying only transient failures.

        Only GET is retried (it is safe/idempotent).  Non-transient HTTP
        responses and exhausted retries return the response unchanged, just
        like a plain single request.
        """

        if transport is None:
            transport = self.session.get

        for attempt in range(self.retries + 1):
            try:
                response = transport(url)
            except requests.RequestException:
                # Connection/timeout style errors are safe to retry for GET.
                if attempt < self.retries:
                    self._sleep(attempt)
                    continue
                raise

            status = getattr(response, "status_code", None)
            if (
                isinstance(status, int)
                and status in RETRY_STATUSES
                and attempt < self.retries
            ):
                self._sleep(attempt)
                continue
            return response

        # Defensive fall-through; the loop above always returns or raises.
        return response

    def _sleep(self, attempt):
        time.sleep(self.backoff * (2**attempt))

    # ------------------------------------------------------------------
    # Coin id resolution
    # ------------------------------------------------------------------

    def get_coin_id(self, coin_code, coin_name, parser=None, transport=None):
        """Resolve a coin code/name to its numeric CMC cryptocurrency id."""

        api_url = (
            f"{CMC_DATA_API}/cryptocurrency/listing?start=1&limit=5000"
            "&sortBy=market_cap&sortType=desc&convert=USD"
            "&cryptoType=all&tagType=all&audited=false"
        )

        json_data = self.get(api_url, transport=transport).json()
        if parser is None:
            from .parser import Parser

            parser = Parser()
        return parser.parse_coin_id(json_data, coin_code, coin_name)

    # ------------------------------------------------------------------
    # Historical data: date handling, pagination and caching
    # ------------------------------------------------------------------

    def fetch_history(
        self,
        coin_code,
        start_date,
        end_date,
        fiat,
        coin_name,
        id_number=None,
        parser=None,
        transport=None,
    ):
        """Fetch and stitch together all yearly chunks for a date range.

        ``start_date``/``end_date`` are ``dd-mm-yyyy`` strings and may be
        ``None`` (all time).  Returns the assembled CMC response ``dict``.
        """

        if start_date is None:
            # default start date on coinmarketcap.com
            start_date = "28-4-2013"
        if end_date is None:
            end_date = datetime.date.today().strftime("%d-%m-%Y")

        convert_id = self.fiat.id_for(fiat)
        if convert_id is None:
            raise ValueError(
                f"Unknown fiat '{fiat}'. Supported: {self.fiat.supported}."
            )

        start_dt = datetime.datetime.strptime(start_date, "%d-%m-%Y").replace(
            tzinfo=datetime.timezone.utc
        )
        end_dt = datetime.datetime.strptime(end_date, "%d-%m-%Y").replace(
            tzinfo=datetime.timezone.utc
        )

        pages, first_page = self.fetch_pages(
            coin_code=coin_code,
            coin_name=coin_name,
            id_number=id_number,
            convert_id=convert_id,
            start_dt=start_dt,
            end_dt=end_dt,
            parser=parser,
            transport=transport,
            fiat=fiat,
        )

        result_json = copy.deepcopy(first_page)
        result_json["data"]["quotes"] = [
            quote for page in pages for quote in page["data"]["quotes"]
        ]
        return result_json

    def fetch_pages(
        self,
        convert_id,
        start_dt,
        end_dt,
        coin_code=None,
        coin_name=None,
        id_number=None,
        parser=None,
        transport=None,
        fiat=None,
        coin_id=None,
    ):
        """Iterate yearly chunks for one range and stitch the pages.

        A cached (and fully assembled) value is served when available.
        Otherwise every page is fetched in order; the cache is written exactly
        once and only after the whole range succeeds, so a failure midway can
        never leave a half-written entry.
        """

        if parser is None:
            from .parser import Parser

            parser = Parser()

        cache_key = None
        if self.cache_dir is not None:
            # Lookup key depends only on coin identity (code/name, falling
            # back to id_number), fiat and the interval -- never on a network
            # resolved coin id -- so a cache hit needs zero listing calls.
            resolved_id = coin_id if coin_id is not None else id_number
            cache_key = self._cache_key(
                coin_code=coin_code,
                coin_name=coin_name,
                id_number=resolved_id,
                fiat=fiat,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            cached = self._cache_read(cache_key)
            if cached is not None:
                return cached[2], cached[1]

        if coin_id is None:
            coin_id = (
                id_number
                if id_number
                else self.get_coin_id(coin_code, coin_name, parser, transport)
            )

        all_quotes = []
        pages = []
        result_json = None
        # timeStart is exclusive (first returned day = timeStart + 1), so each
        # chunk's timeStart equals the previous chunk's timeEnd -- no overlap.
        chunk_start = start_dt - datetime.timedelta(days=1)

        while chunk_start < end_dt:
            chunk_end = min(chunk_start + datetime.timedelta(days=CHUNK_DAYS), end_dt)
            api_url = (
                f"{CMC_DATA_API}/cryptocurrency/historical"
                f"?id={coin_id}&convertId={convert_id}&timeStart={int(chunk_start.timestamp())}&timeEnd={int(chunk_end.timestamp())}"
            )
            json_data = self.get(api_url, transport=transport).json()
            parser.check_status(json_data)
            pages.append(json_data)
            all_quotes.extend(json_data["data"]["quotes"])
            if result_json is None:
                result_json = json_data
            chunk_start = chunk_end + datetime.timedelta(days=1)

        if cache_key is not None:
            # Atomic replace: readers only ever see a complete file.
            self._cache_write(cache_key, (coin_id, result_json, pages))

        return pages, result_json

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cache_key(coin_code, coin_name, id_number, fiat, start_dt, end_dt):
        """Stable key covering coin, fiat and interval (no network data)."""
        coin_token = (
            f"id{id_number}"
            if id_number
            else ("{}|{}".format((coin_code or "").upper(), (coin_name or "").lower()))
        )
        digest = hashlib.sha256()
        for part in (
            coin_token,
            (fiat or "").upper(),
            start_dt.strftime("%Y-%m-%d"),
            end_dt.strftime("%Y-%m-%d"),
        ):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
        label = f"id{id_number}" if id_number else (coin_code or "coin")
        return "{}_{}_{}_{}_{}.json".format(
            label,
            (fiat or "").upper(),
            start_dt.strftime("%Y%m%d"),
            end_dt.strftime("%Y%m%d"),
            digest.hexdigest()[:12],
        )

    def _cache_path(self, key):
        safe = key.replace(os.sep, "_").replace("/", "_")
        return os.path.join(self.cache_dir, safe)

    def _get_cache_lock(self, key):
        with self._cache_locks_guard:
            lock = self._cache_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._cache_locks[key] = lock
            return lock

    def _cache_read(self, key):
        path = self._cache_path(key)
        lock = self._get_cache_lock(key)
        with lock:
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    value = json.load(fp)
            except (OSError, ValueError):
                return None
        # Deep copy so callers can never corrupt data another thread reads.
        return copy.deepcopy(value)

    def _cache_write(self, key, value):
        path = self._cache_path(key)
        lock = self._get_cache_lock(key)
        tmp_path = None
        with lock:
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                tmp_path = f"{path}.{os.getpid()}.tmp"
                with open(tmp_path, "w", encoding="utf-8") as fp:
                    json.dump(value, fp)
                    fp.flush()
                    os.fsync(fp.fileno())
                os.replace(tmp_path, path)
            except OSError:
                # A cache write failure must not break a successful fetch.
                if tmp_path is not None:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
