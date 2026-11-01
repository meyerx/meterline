"""Shared HTTP plumbing of the connectors: the request throttle, the retry
back-off, how a ``Retry-After`` header is read and capped, and how secrets are
kept out of error text and URLs.

The core package has no dependencies, so nothing here imports an HTTP library:
each connector keeps its own HTTP client and hands this module status codes,
header values and its injected clocks. Provider differences (the throttle
window, extra rate-limit headers, which statuses carry a limit) stay in the
connector as explicit arguments. One behaviour holds for every connector:

- throttle: at most ``rate`` requests in any closed window of ``window``
  seconds (a request exactly ``window`` seconds old still counts);
- back-off before retry ``n`` (0-based): 1, 2, 4 ... seconds capped at 60,
  scaled to 50-100 percent by the jitter, never below 1;
- ``Retry-After``: delay-seconds or an HTTP-date (RFC 9110), read on every
  retryable status; absent, garbage or not finite (NaN, Infinity) means the
  server named no wait; a past date or a number at or below 0 means 0; never
  more than the cap (300 seconds unless configured);
- the wait before a retry is the larger of the back-off and the wait the
  server named, so a retry is never sooner than either asks.
"""

import email.utils
import math
from datetime import timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, List, Optional
from urllib.parse import urlsplit, urlunsplit

BACKOFF_START_SECONDS = 1
BACKOFF_CAP_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = 300  # longest wait a Retry-After header can ask for
RETRYABLE_STATUSES_FLOOR = 500

__all__ = [
    "BACKOFF_CAP_SECONDS",
    "BACKOFF_START_SECONDS",
    "MAX_RETRY_AFTER_SECONDS",
    "RETRYABLE_STATUSES_FLOOR",
    "FakeClock",
    "Throttle",
    "backoff_seconds",
    "failure_kind_of_status",
    "header_number",
    "is_retryable",
    "mask",
    "retry_after_seconds",
    "retry_wait",
    "without_userinfo",
]


def is_retryable(status: int) -> bool:
    """429 and every 5xx are retried; a connector may add its own cases."""
    return status == 429 or status >= RETRYABLE_STATUSES_FLOOR


def failure_kind_of_status(status: int) -> str:
    """``transient`` for a transport failure (status 0), a status that is not an error, a timeout (408), 429 and every
    5xx: running again later can work. ``permanent`` for any other 4xx (400, 401, 403, 404, 422 ...): the provider
    refused the request, so the key, the scope or the identifier must change first."""
    if status < 400 or status == 408 or is_retryable(status):
        return "transient"
    return "permanent"


def backoff_seconds(attempt: int, jitter: Callable[[], Any]) -> int:
    """Whole seconds to wait before retry number ``attempt`` (0-based)."""
    nominal = min(BACKOFF_START_SECONDS * (2 ** attempt), BACKOFF_CAP_SECONDS)
    percent = min(max(50 + int(jitter() * 51), 50), 100)
    return max(1, nominal * percent // 100)


def header_number(raw: Optional[str]) -> Optional[Decimal]:
    """A header value as a ``Decimal``, or None when it is absent or not a number."""
    if raw is None:
        return None
    try:
        return Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        return None


def retry_after_seconds(
    raw: Optional[str], *, wall: Callable[[], Any], cap: int = MAX_RETRY_AFTER_SECONDS
) -> Optional[int]:
    """Whole seconds a ``Retry-After`` value asks for, never more than ``cap``;
    None when the value names no usable wait. An HTTP-date is read against the
    injected wall clock, in ``Decimal`` (no binary floating point)."""
    if raw is None:
        return None
    text = raw.strip()
    number = header_number(text)
    if number is not None:
        if not number.is_finite():
            return None
        if number <= 0:  # compare before rounding: ceil of -1e999999999 never returns
            return 0
        if number > cap:  # compare before rounding: ceil of 1e999999999 never returns
            return cap
        return math.ceil(number)
    try:
        when = email.utils.parsedate_to_datetime(text)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        wait = math.ceil(Decimal(str(when.timestamp())) - Decimal(str(wall())))
    except (TypeError, ValueError, IndexError, OverflowError, AttributeError, ArithmeticError):
        return None
    return min(max(0, wait), cap)


def retry_wait(backoff: int, server_wait: Optional[int]) -> int:
    """Seconds before the next try: the larger of the back-off and the wait the
    server named (None when it named none)."""
    return max(backoff, server_wait or 0)


class Throttle:
    """Blocks (through the injected ``sleep``) until a request fits: at most
    ``rate`` requests in any closed window of ``window`` seconds."""

    def __init__(self, rate: int, window: int, *, clock: Callable[[], Any], sleep: Callable[[Any], Any]) -> None:
        if rate < 1:
            raise ValueError("the request rate must be at least 1")
        self.rate = rate
        self.window = window
        self._clock = clock
        self._sleep = sleep
        self._stamps: List[Any] = []

    def wait(self) -> None:
        while True:
            now = self._clock()
            self._stamps = [t for t in self._stamps if now - t <= self.window]
            if len(self._stamps) < self.rate:
                break
            self._sleep(max(math.ceil(self._stamps[0] + self.window - now), 1))
        self._stamps.append(self._clock())


class FakeClock:
    """A clock for tests whose ``sleep`` moves it, so a wait costs no real time.

    Hand it to a connector as ``clock=clock, sleep=clock.sleep, wall=clock``: the throttle, the back-off and the wait a
    ``Retry-After`` names then run against this clock. ``sleeps`` lists every wait asked for, in order. With a ``sleep``
    that does nothing and the real clock instead, a full throttle would spin until the real window had passed.
    """

    def __init__(self, start: int = 1_000_000) -> None:
        self.now = start
        self.sleeps: List[Any] = []

    def __call__(self) -> Any:
        return self.now

    def sleep(self, seconds: Any) -> None:
        self.sleeps.append(seconds)
        self.now += max(seconds, 0)

    def advance(self, seconds: Any) -> None:
        self.now += seconds


def mask(value: Any, secrets: Iterable[Optional[str]]) -> Any:
    """``value`` with every secret replaced by ``***`` in every string, dict key
    and nested item: an error body is kept on an exception, so it must not carry
    a key."""
    found = sorted({s for s in secrets if s}, key=len, reverse=True)  # longest first: a secret inside another leaves no tail
    if isinstance(value, str):
        for secret in found:
            value = value.replace(secret, "***")
        return value
    if isinstance(value, dict):
        return {mask(k, found): mask(v, found) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v, found) for v in value]
    return value


def without_userinfo(url: str, *, keep_query: bool = True, keep_fragment: bool = True) -> str:
    """The URL without a user name or password and, when asked, without its
    query string (signed links carry their signature there) or fragment."""
    parts = urlsplit(url)
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query if keep_query else "",
                       parts.fragment if keep_fragment else ""))
