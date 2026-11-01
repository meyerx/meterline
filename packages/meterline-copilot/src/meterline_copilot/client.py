"""HTTP client for the GitHub REST API (Copilot usage reports, billing, seats).

Sends the token as ``Authorization: Bearer <token>`` to ``api.github.com`` and
nowhere else: the signed download links of a usage report are fetched by a
second client that sends no ``Authorization`` header. Two tokens are held (one
for usage reports and seats, one for billing). Requests are throttled to a
configurable hourly rate; a 403 or 429 that carries ``retry-after`` or an
exhausted ``x-ratelimit-reset`` is waited out, other 429 and 5xx are retried
with back-off. Throttle, back-off and ``retry-after`` follow the one rule of
``meterline.http`` shared by every connector. Everything that touches time is
injectable.

A token is only ever placed in the ``Authorization`` header. It is never logged
and never appears in an exception message (any echo of it in an error body is
masked). JSON numbers are parsed straight into ``Decimal``.
"""

import json
import logging
import math
import random
import time
from decimal import Decimal
from typing import Any, Callable, Dict, NamedTuple, Optional
from urllib.parse import urlsplit

import httpx
from meterline import http

from . import __version__

logger = logging.getLogger("meterline_copilot.client")

API_BASE = "https://api.github.com"
API_VERSION = "2026-03-10"

MAX_WAIT_SECONDS = 7200  # longest wait for a rate-limit reset (x-ratelimit-reset)
MAX_RETRY_AFTER_SECONDS = http.MAX_RETRY_AFTER_SECONDS  # longest wait a Retry-After header can ask for
WINDOW_SECONDS = 3600  # the rate is per hour


class ApiError(Exception):
    """A non-retryable (or exhausted) API failure. ``body`` is the response
    text, cut and masked; the message never contains a token."""

    def __init__(self, status: int, body: Any = None, message: Optional[str] = None) -> None:
        self.status = status
        self.body = body
        super().__init__(message if message is not None else "HTTP %d" % status)


class UnusableBody(ApiError):
    """A 200 whose body is not JSON, or JSON that breaks the documented shape."""


class Page(NamedTuple):
    """One response: HTTP status, the text exactly as received, parsed body,
    the request URL without query secrets or userinfo, lower-cased headers."""

    status: int
    text: str
    body: Any
    url: str
    headers: Dict[str, str]


def clean_url(url: str, *, keep_query: bool = True) -> str:
    """The URL without userinfo and, unless asked, without a query string
    (signed download links carry their signature there)."""
    return http.without_userinfo(url, keep_query=keep_query, keep_fragment=False)


class CopilotClient:
    def __init__(
        self,
        token: str,
        *,
        billing_token: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
        rate_per_hour: int = 600,
        clock: Callable[[], Any] = time.monotonic,
        sleep: Callable[[Any], Any] = time.sleep,
        wall: Callable[[], Any] = time.time,
        jitter: Callable[[], Any] = random.random,
        max_tries: int = 6,
        max_retry_after: int = MAX_RETRY_AFTER_SECONDS,
    ) -> None:
        if max_retry_after < 0:
            raise ValueError("max_retry_after must not be negative")
        self._max_retry_after = max_retry_after
        if not token:
            raise ValueError("a token is required")
        if rate_per_hour < 1:
            raise ValueError("rate_per_hour must be at least 1")
        self._tokens = {False: token, True: billing_token or token}
        self._throttle = http.Throttle(rate_per_hour, WINDOW_SECONDS, clock=clock, sleep=sleep).wait
        self._sleep = sleep
        self._wall = wall
        self._jitter = jitter
        self._max_tries = max_tries
        self.request_count = 0
        agent = "meterline-copilot/%s" % __version__
        self._api = httpx.Client(
            transport=transport, base_url=API_BASE + "/", timeout=60,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION, "User-Agent": agent},
        )
        # the download hosts get no credential of any kind
        self._files = httpx.Client(transport=transport, timeout=120, headers={"User-Agent": agent})

    def close(self) -> None:
        self._api.close()
        self._files.close()

    def __enter__(self) -> "CopilotClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- helpers ------------------------------------------------------------

    def _mask(self, text: str) -> str:
        return http.mask(text, self._tokens.values())

    def _backoff(self, attempt: int) -> int:
        return http.backoff_seconds(attempt, self._jitter)

    def _retry_after(self, response: httpx.Response) -> Optional[int]:
        """Whole seconds ``Retry-After`` asks for (capped), or None (see ``meterline.http``)."""
        return http.retry_after_seconds(response.headers.get("retry-after"), wall=self._wall, cap=self._max_retry_after)

    def _limit_wait(self, response: httpx.Response) -> Optional[int]:
        """Whole seconds the API asks for (``retry-after``, or the time to
        ``x-ratelimit-reset``), or None when the response names no limit."""
        after = self._retry_after(response)
        if after is not None:
            return after
        reset = http.header_number(response.headers.get("x-ratelimit-reset"))
        remaining = response.headers.get("x-ratelimit-remaining")
        if reset is not None and reset.is_finite() and (response.status_code == 429 or (remaining or "").strip() == "0"):
            now = Decimal(str(self._wall()))
            if reset <= now:  # compare before any arithmetic: a huge exponent overflows or never rounds
                return 0
            if reset > now + MAX_WAIT_SECONDS:
                return MAX_WAIT_SECONDS + 1  # _retry refuses anything above the cap
            return math.ceil(reset - now)
        return None

    def _retry(self, response: httpx.Response, attempt: int) -> Optional[int]:
        """Seconds to wait before the next try, or None when the status is not
        retryable. GitHub signals a rate limit on 403 as well as 429, and may
        name it in ``x-ratelimit-reset`` instead of ``retry-after``; a 403
        that names no limit is a refusal. The wait itself follows the shared
        rule: the larger of the back-off and the wait the server named."""
        status = response.status_code
        if status in (403, 429):
            wait = self._limit_wait(response)
            if wait is not None and wait > MAX_WAIT_SECONDS:
                raise ApiError(status, None, "rate limit reset is more than %d seconds away" % MAX_WAIT_SECONDS)
            if wait is None and status == 403:
                return None
            return http.retry_wait(self._backoff(attempt), wait)
        if http.is_retryable(status):
            return http.retry_wait(self._backoff(attempt), self._retry_after(response))
        return None

    # ---- requests -------------------------------------------------------------

    def get_page(self, path: str, params: Optional[Dict[str, Any]] = None, *, billing: bool = False) -> Page:
        """GET one API response with throttle and retry; keeps the raw text.
        A 204 gives an empty page (status 204, body ``{}``)."""
        headers = {"Authorization": "Bearer " + self._tokens[billing]}
        last_status = 0
        for attempt in range(self._max_tries):
            self._throttle()
            self.request_count += 1
            wait = 0
            try:
                response = self._api.get(path.lstrip("/"), params=params, headers=headers)
            except httpx.TransportError as exc:
                last_status = 0
                wait = self._backoff(attempt)
                logger.warning("transport error %s (attempt %d)", type(exc).__name__, attempt + 1)
            else:
                status = response.status_code
                if status == 204:
                    return Page(204, "", {}, clean_url(str(response.url)), dict(response.headers))
                if status == 200:
                    text = response.text
                    try:
                        body = json.loads(text, parse_float=Decimal)
                    except ValueError:
                        raise UnusableBody(status, None, "HTTP 200 with a body that is not JSON") from None
                    return Page(200, text, body, clean_url(str(response.url)), {k.lower(): v for k, v in response.headers.items()})
                retry = self._retry(response, attempt)
                if retry is None:
                    raise ApiError(status, self._mask(response.text[:200]), "HTTP %d" % status)
                last_status = status
                wait = retry
                logger.warning("retryable status %d (attempt %d)", status, attempt + 1)
            if attempt + 1 < self._max_tries:
                self._sleep(wait)
        raise ApiError(last_status, None, "giving up after %d tries (last status %d)" % (self._max_tries, last_status))

    def get_json(self, path: str, params: Optional[Dict[str, Any]] = None, *, billing: bool = False) -> Any:
        return self.get_page(path, params, billing=billing).body

    def download(self, url: str) -> str:
        """GET a signed download link, with no credential, and return its text."""
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.netloc:
            raise UnusableBody(200, None, "a download link is not an https URL")
        last_status = 0
        for attempt in range(self._max_tries):
            wait = 0
            try:
                response = self._files.get(url)
            except httpx.TransportError as exc:
                last_status = 0
                wait = self._backoff(attempt)
                logger.warning("download transport error %s (attempt %d)", type(exc).__name__, attempt + 1)
            else:
                status = response.status_code
                if status == 200:
                    return response.text
                if http.is_retryable(status):
                    last_status = status
                    wait = http.retry_wait(self._backoff(attempt), self._retry_after(response))
                    logger.warning("download retryable status %d (attempt %d)", status, attempt + 1)
                else:
                    raise ApiError(status, None, "download HTTP %d" % status)
            if attempt + 1 < self._max_tries:
                self._sleep(wait)
        raise ApiError(last_status, None, "download giving up after %d tries (last status %d)" % (self._max_tries, last_status))
