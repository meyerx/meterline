"""HTTP client for the Enterprise Analytics API.

Handles the x-api-key header, the per-minute throttle, retry with back-off,
and cursor pagination. Everything that touches time is injectable.

The key is only ever placed in the ``x-api-key`` header. It is never logged and
never appears in an exception message (any echo of it in an error body is
masked).
"""

import json
import logging
import random
import re
import time
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Dict, Iterator, List, NamedTuple, Optional

import httpx
from meterline import http

from . import __version__

logger = logging.getLogger("meterline_anthropic.client")

DEFAULT_BASE_URL = "https://api.anthropic.com/v1/organizations/analytics/"
API_VERSION = "2023-06-01"
MAX_RESTARTS = 3
MAX_PAGES = 10000  # pages of one query; more is an error, not a long wait
MAX_RETRY_AFTER_SECONDS = http.MAX_RETRY_AFTER_SECONDS  # longest wait a Retry-After header can ask for
WINDOW_SECONDS = 60  # the rate limit is per minute

_DATE_IN_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")


class ApiError(Exception):
    """A non-retryable (or exhausted) API failure. ``body`` is the parsed
    response (or text); the message never contains the API key."""

    def __init__(self, status: int, body: Any = None, message: Optional[str] = None) -> None:
        self.status = status
        self.body = body
        super().__init__(message if message is not None else "HTTP %d" % status)


class PaginationError(ValueError):
    """A query's pages do not add up: the cursor repeats, or there are more
    pages than ``MAX_PAGES``. Nothing from the query is used (exit 2)."""


class ActivityDateUnavailable(ApiError):
    """HTTP 400 whose message names the latest available date."""

    def __init__(self, latest_date: date, body: Any = None) -> None:
        self.latest_date = latest_date
        super().__init__(
            400, body, "requested date not yet available; latest available date is %s" % latest_date.isoformat()
        )


class Page(NamedTuple):
    """One response: HTTP status, the text exactly as received, parsed body."""

    status: int
    text: str
    body: Dict[str, Any]
    url: str


def _error_text(body: Any) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
    return body if isinstance(body, str) else ""


class AnalyticsClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        base_url: str = DEFAULT_BASE_URL,
        rate_per_minute: int = 50,
        clock: Callable[[], Any] = time.monotonic,
        sleep: Callable[[Any], Any] = time.sleep,
        jitter: Callable[[], Any] = random.random,
        max_tries: int = 6,
        wall: Callable[[], Any] = time.time,
        max_retry_after: int = MAX_RETRY_AFTER_SECONDS,
        max_pages: Optional[int] = None,
    ) -> None:
        if not api_key:
            raise ValueError("an API key is required")
        if rate_per_minute < 1:
            raise ValueError("rate_per_minute must be at least 1")
        if max_retry_after < 0:
            raise ValueError("max_retry_after must not be negative")
        self._wall = wall
        self._max_retry_after = max_retry_after
        self._max_pages = max_pages
        self._key = api_key
        self._throttle = http.Throttle(rate_per_minute, WINDOW_SECONDS, clock=clock, sleep=sleep).wait
        self._sleep = sleep
        self._jitter = jitter
        self._max_tries = max_tries
        self.request_count = 0
        self._http = httpx.Client(
            transport=transport,
            base_url=base_url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
                "User-Agent": "meterline-anthropic/%s" % __version__,
            },
            timeout=60,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "AnalyticsClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- helpers ---------------------------------------------------------

    def _mask(self, text: str) -> str:
        return http.mask(text, [self._key])

    def _mask_value(self, value: Any) -> Any:
        """``value`` with the key masked in every string, key and nested item:
        an error body is kept on the exception, so it must not carry the key."""
        return http.mask(value, [self._key])

    # ---- requests --------------------------------------------------------

    def get_page(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Page:
        """GET one response with throttle and retry; returns the raw text too."""
        last_status = 0
        last_body: Any = None
        for attempt in range(self._max_tries):
            self._throttle()
            self.request_count += 1
            wait_floor: Optional[int] = None
            try:
                response = self._http.get(endpoint, params=params)
            except httpx.TransportError as exc:
                last_status, last_body = 0, type(exc).__name__
                logger.warning("transport error %s (attempt %d)", type(exc).__name__, attempt + 1)
            else:
                status = response.status_code
                text = response.text
                if status == 200:
                    try:
                        body = json.loads(text, parse_float=Decimal)
                    except ValueError:
                        raise ApiError(status, None, "HTTP 200 with a body that is not JSON") from None
                    return Page(status, text, body, str(response.url))
                try:
                    err_body: Any = self._mask_value(json.loads(text, parse_float=Decimal))
                except ValueError:
                    err_body = self._mask(text[:200])
                if http.is_retryable(status):
                    last_status, last_body = status, err_body
                    wait_floor = http.retry_after_seconds(
                        response.headers.get("retry-after"), wall=self._wall, cap=self._max_retry_after)
                    logger.warning("retryable status %d (attempt %d)", status, attempt + 1)
                else:
                    self._raise_for(status, err_body)
            if attempt + 1 < self._max_tries:
                self._sleep(http.retry_wait(http.backoff_seconds(attempt, self._jitter), wait_floor))
        raise ApiError(last_status, last_body, "giving up after %d tries (last status %d)" % (self._max_tries, last_status))

    def _raise_for(self, status: int, body: Any) -> None:
        text = self._mask(_error_text(body))
        if status == 400:
            found = _DATE_IN_TEXT.search(text)
            if found:
                try:
                    raise ActivityDateUnavailable(date.fromisoformat(found.group(0)), body)
                except ValueError:
                    pass
        raise ApiError(status, body, ("HTTP %d: %s" % (status, text[:200])) if text else None)

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET one response; returns the parsed JSON body (floats as Decimal)."""
        return self.get_page(endpoint, params).body

    def fetch_pages(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> List[Page]:
        """All pages of one query. Follows ``next_page`` while it is not null
        and ``has_more`` is not false (a response without ``has_more`` pages on
        ``next_page`` alone), with every other parameter unchanged. A cursor
        that comes back, or more than ``MAX_PAGES`` pages, raises
        ``PaginationError``. On HTTP 410 the collected pages
        are dropped and the query restarts from page 1 (at most 3 restarts).
        Pages are collected before anything is returned, so a restart can never
        hand the caller a page twice."""
        base = dict(params or {})
        restarts = 0
        limit = MAX_PAGES if self._max_pages is None else self._max_pages
        while True:
            collected: List[Page] = []
            seen: set = set()
            query = dict(base)
            try:
                while True:
                    page = self.get_page(endpoint, query)
                    collected.append(page)
                    body = page.body
                    more = body.get("has_more", True)
                    if more and body.get("next_page"):
                        cursor = body["next_page"]
                        if cursor in seen or cursor == query.get("page"):
                            raise PaginationError("%s: the cursor repeats after %d pages" % (endpoint, len(collected)))
                        if len(collected) >= limit:
                            raise PaginationError("%s: more than %d pages in one query" % (endpoint, limit))
                        seen.add(cursor)
                        query = dict(base)
                        query["page"] = cursor
                    else:
                        break
            except ApiError as exc:
                if exc.status != 410:
                    raise
                restarts += 1
                logger.warning("cursor expired; restart %d of %d", restarts, MAX_RESTARTS)
                if restarts > MAX_RESTARTS:
                    raise ApiError(410, exc.body, "cursor kept expiring after %d restarts" % MAX_RESTARTS) from None
                continue
            return collected

    def pages(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Iterate the parsed bodies of every page (see ``fetch_pages``)."""
        for page in self.fetch_pages(endpoint, params):
            yield page.body
