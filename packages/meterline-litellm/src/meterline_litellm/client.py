"""HTTP client for a LiteLLM proxy's admin API.

Sends the key as ``Authorization: Bearer <key>``, throttles to a configurable
rate, and retries 429 and 5xx with back-off (honouring ``retry-after``).
Everything that touches time is injectable.

The key is only ever placed in the ``Authorization`` header. It is never logged
and never appears in an exception message (any echo of it in an error body is
masked). JSON numbers are parsed straight into ``Decimal``.
"""

import json
import logging
import random
import time
from decimal import Decimal
from typing import Any, Callable, Dict, NamedTuple, Optional

import httpx
from meterline import http

from . import __version__

logger = logging.getLogger("meterline_litellm.client")

MAX_RETRY_AFTER_SECONDS = http.MAX_RETRY_AFTER_SECONDS  # longest wait a Retry-After header can ask for
WINDOW_SECONDS = 60  # the rate is per minute


def _without_userinfo(url: str) -> str:
    """The base URL without a user name or password: the only credential this
    client sends is the bearer key."""
    return http.without_userinfo(url)


class ApiError(Exception):
    """A non-retryable (or exhausted) API failure. ``body`` is the parsed
    response (or text); the message never contains the key."""

    def __init__(self, status: int, body: Any = None, message: Optional[str] = None) -> None:
        self.status = status
        self.body = body
        super().__init__(message if message is not None else "HTTP %d" % status)


class PaginationError(ValueError):
    """A query's pages do not add up: the server repeats a page, answers a page
    other than the one asked for, or there are more pages than the cap. Nothing
    from the query is used (exit 2)."""


class Page(NamedTuple):
    """One response: HTTP status, the text exactly as received, parsed body."""

    status: int
    text: str
    body: Dict[str, Any]
    url: str


class LitellmClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        transport: Optional[httpx.BaseTransport] = None,
        rate_per_minute: int = 120,
        clock: Callable[[], Any] = time.monotonic,
        sleep: Callable[[Any], Any] = time.sleep,
        jitter: Callable[[], Any] = random.random,
        max_tries: int = 6,
        wall: Callable[[], Any] = time.time,
        max_retry_after: int = MAX_RETRY_AFTER_SECONDS,
    ) -> None:
        if max_retry_after < 0:
            raise ValueError("max_retry_after must not be negative")
        self._wall = wall
        self._max_retry_after = max_retry_after
        if not api_key:
            raise ValueError("an API key is required")
        if not base_url:
            raise ValueError("a base URL is required")
        if rate_per_minute < 1:
            raise ValueError("rate_per_minute must be at least 1")
        self._key = api_key
        self._throttle = http.Throttle(rate_per_minute, WINDOW_SECONDS, clock=clock, sleep=sleep).wait
        self._sleep = sleep
        self._jitter = jitter
        self._max_tries = max_tries
        self.request_count = 0
        self._http = httpx.Client(
            transport=transport,
            base_url=_without_userinfo(base_url).rstrip("/") + "/",
            headers={
                "Authorization": "Bearer " + api_key,
                "User-Agent": "meterline-litellm/%s" % __version__,
            },
            timeout=60,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "LitellmClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _mask(self, text: str) -> str:
        return http.mask(text, [self._key])

    def get_page(self, path: str, params: Optional[Dict[str, Any]] = None) -> Page:
        """GET one response with throttle and retry; returns the raw text too."""
        last_status = 0
        for attempt in range(self._max_tries):
            self._throttle()
            self.request_count += 1
            wait_floor: Optional[int] = None
            try:
                response = self._http.get(path, params=params)
            except httpx.TransportError as exc:
                last_status = 0
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
                if http.is_retryable(status):
                    last_status = status
                    wait_floor = http.retry_after_seconds(
                        response.headers.get("retry-after"), wall=self._wall, cap=self._max_retry_after)
                    logger.warning("retryable status %d (attempt %d)", status, attempt + 1)
                else:
                    raise ApiError(status, self._mask(text[:200]), "HTTP %d" % status)
            if attempt + 1 < self._max_tries:
                self._sleep(http.retry_wait(http.backoff_seconds(attempt, self._jitter), wait_floor))
        raise ApiError(last_status, None, "giving up after %d tries (last status %d)" % (self._max_tries, last_status))

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET one response; returns the parsed JSON body (numbers with a
        fraction as ``Decimal``)."""
        return self.get_page(path, params).body
