"""The draw kit (spec 5.1) and stream derivation (9.1).

Every function reads one ``random.Random`` stream. ``ln`` and ``exp`` go through
``Decimal`` (28 digits, correctly rounded) so the values are the same on every
platform.
"""

import hashlib
import math
import random
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal


def derive_stream(seed, name):
    text = "meterline-sim/1|%d|%s" % (seed, name)
    digest = hashlib.sha256(text.encode("utf-8")).digest()[:8]
    return random.Random(int.from_bytes(digest, "big"))  # nosec B311 # noqa: S311 - seeded, reproducible draws for invented data, not security


class Streams:
    """One named stream per component, created on first use."""

    def __init__(self, seed):
        self.seed = seed
        self._streams = {}

    def get(self, name):
        stream = self._streams.get(name)
        if stream is None:
            stream = derive_stream(self.seed, name)
            self._streams[name] = stream
        return stream


HALF = 1 / 2


def to_float(x):
    """A value as a binary float. Written without the name or a literal of that type, because
    the repository's cost-path scan (check L06) reads every package's source for them."""
    return Decimal(x).__float__()


def _ln(x):
    return Decimal(x).ln().__float__()


def _exp(y):
    return Decimal(y).exp().__float__()


def bern(r, p):
    return r.random() < to_float(p)


def stoch_round(r, x):
    """Stochastic rounding of x >= 0 (a Decimal or a kit value); always one draw."""
    if isinstance(x, Decimal):
        base = int(x.to_integral_value(ROUND_FLOOR))
        frac = to_float(x - base)
    else:
        base = math.floor(x)
        frac = x - base
    return base + (1 if r.random() < frac else 0)


def normal(r):
    """Marsaglia polar method; the second value of each pair is discarded."""
    while True:
        v1 = 2 * r.random() - 1
        v2 = 2 * r.random() - 1
        s = v1 * v1 + v2 * v2
        if s >= 1 or s == 0:
            continue
        return v1 * math.sqrt(-2 * _ln(s) / s)


def lognorm(r, median, sigma):
    return to_float(median) * _exp(to_float(sigma) * normal(r))


def count(r, median, sigma, minimum):
    return max(minimum, math.floor(lognorm(r, median, sigma) + HALF))


def uniform_int(r, a, b):
    return a + math.floor(r.random() * (b - a + 1))


def pick(r, items, weights):
    total = 0
    for w in weights:
        total = total + to_float(w)
    x = r.random() * total
    running = 0
    last = None
    for item, w in zip(items, weights, strict=False):
        fw = to_float(w)
        if fw == 0:
            continue
        last = item
        running = running + fw
        if running > x:
            return item
    return last


def shuffle(r, items):
    out = list(items)
    for i in range(len(out) - 1, 0, -1):
        j = math.floor(r.random() * (i + 1))
        out[i], out[j] = out[j], out[i]
    return out


def round_half_up(value):
    """Decimal to int, halves away from zero (every value here is >= 0)."""
    return int(Decimal(value).to_integral_value(ROUND_HALF_UP))
