"""HTTP client for the Anthropic Admin Usage and Cost API.

Handles the x-api-key header, the per-minute throttle, retry with back-off, and cursor pagination. Everything that
touches time is injectable.

The key is only ever placed in the ``x-api-key`` header. It is never logged and never appears in an exception message
(any echo of it in an error body is masked).
"""

import json
import logging
import random
import time
from decimal import Decimal
from typing import Any, Callable, Dict, Iterator, List, NamedTuple, Optional

import httpx
from meterline import http

from . import __version__

logger = logging.getLogger("meterline_anthropic_admin.client")

DEFAULT_BASE_URL = "https://api.anthropic.com"
USAGE_PATH = "/v1/organizations/usage_report/messages"
COST_PATH = "/v1/organizations/cost_report"
API_VERSION = "2023-06-01"
MAX_PAGES = 10000  # pages of one query; more is an error, not a long wait
MAX_RETRY_AFTER_SECONDS = http.MAX_RETRY_AFTER_SECONDS  # longest wait a Retry-After header can ask for
WINDOW_SECONDS = 60  # the documented polling rate is per minute


class ApiError(Exception):
    """A non-retryable (or exhausted) API failure. ``body`` is the parsed response (or text); the message never
    contains the API key."""

    def __init__(self, status: int, body: Any = None, message: Optional[str] = None) -> None:
        self.status = status
        self.body = body
        super().__init__(message if message is not None else "HTTP %d" % status)


class PaginationError(ValueError):
    """A query's pages do not add up: the cursor repeats, or there are more pages than ``MAX_PAGES``. Nothing from
    the query is used (exit 2)."""


class Page(NamedTuple):
    """One response: HTTP status, the text exactly as received, parsed body, request URL."""

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


class AdminClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        base_url: str = DEFAULT_BASE_URL,
        rate_per_minute: int = 20,
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
                "User-Agent": "meterline-anthropic-admin/%s" % __version__,
            },
            timeout=60,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "AdminClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _mask(self, text: str) -> str:
        return http.mask(text, [self._key])

    def _mask_value(self, value: Any) -> Any:
        """``value`` with the key masked in every string, key and nested item: an error body is kept on the
        exception, so it must not carry the key."""
        return http.mask(value, [self._key])

    def get_page(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Page:
        """GET one response with throttle and retry; returns the raw text too. A list value is sent as repeated
        parameters, so ``{"group_by[]": ["a", "b"]}`` becomes ``group_by[]=a&group_by[]=b``."""
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
                if not http.is_retryable(status):
                    message = self._mask(_error_text(err_body))
                    raise ApiError(status, err_body, ("HTTP %d: %s" % (status, message[:200])) if message else None)
                last_status, last_body = status, err_body
                wait_floor = http.retry_after_seconds(response.headers.get("retry-after"), wall=self._wall, cap=self._max_retry_after)
                logger.warning("retryable status %d (attempt %d)", status, attempt + 1)
            if attempt + 1 < self._max_tries:
                self._sleep(http.retry_wait(http.backoff_seconds(attempt, self._jitter), wait_floor))
        raise ApiError(last_status, last_body, "giving up after %d tries (last status %d)" % (self._max_tries, last_status))

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET one response; returns the parsed JSON body (decimals as Decimal)."""
        return self.get_page(endpoint, params).body

    def fetch_pages(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> List[Page]:
        """All pages of one query. Follows ``next_page`` while ``has_more`` is true, with every other parameter
        unchanged. A cursor that comes back, or more than ``MAX_PAGES`` pages, raises ``PaginationError``."""
        base = dict(params or {})
        limit = MAX_PAGES if self._max_pages is None else self._max_pages
        collected: List[Page] = []
        seen: set = set()
        query = dict(base)
        while True:
            page = self.get_page(endpoint, query)
            collected.append(page)
            body = page.body
            cursor = body.get("next_page")
            if not (body.get("has_more") and cursor):
                return collected
            if cursor in seen or cursor == query.get("page"):
                raise PaginationError("%s: the cursor repeats after %d pages" % (endpoint, len(collected)))
            if len(collected) >= limit:
                raise PaginationError("%s: more than %d pages in one query" % (endpoint, limit))
            seen.add(cursor)
            query = dict(base)
            query["page"] = cursor

    def pages(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Iterate the parsed bodies of every page (see ``fetch_pages``)."""
        for page in self.fetch_pages(endpoint, params):
            yield page.body
