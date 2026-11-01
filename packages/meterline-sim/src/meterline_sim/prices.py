"""The price table and the pricing of usage rows (spec 8.1, 8.2). Money is Decimal only."""

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

from meterline_sim.params import DATA, ParamError, canonical_sha, load_json_file

BUCKETS = ("input_uncached", "cache_read", "cache_write_5m", "cache_write_1h", "output")
_ROW_QUANT = Decimal("0.00000001")
_PRICE_QUANT = Decimal("0.0001")
MILLION = Decimal(1000000)


def _price(value, where, max_decimals=4):
    if isinstance(value, bool) or not isinstance(value, str):
        raise ParamError("price table: %s must be a decimal string" % where)
    try:
        out = Decimal(value)
    except InvalidOperation as exc:
        raise ParamError("price table: %s is not a decimal string" % where) from exc
    if not out.is_finite() or out < 0 or out.as_tuple().exponent < -max_decimals:
        raise ParamError("price table: %s needs a non-negative value with at most %d decimals" % (where, max_decimals))
    return out


class PriceTable:
    def __init__(self, obj):
        self.obj = obj
        self.sha256 = canonical_sha(obj)
        if not isinstance(obj, dict) or obj.get("price_table_format") != 1:
            raise ParamError("price table: price_table_format must be 1")
        if obj.get("currency") != "USD":
            raise ParamError("price table: currency must be USD")
        self.entries = []
        for n, item in enumerate(obj.get("token_prices", [])):
            where = "token_prices[%d]" % n
            try:
                valid_from = date.fromisoformat(item["valid_from"])
                provider, model = item["provider"], item["model"]
            except (KeyError, TypeError, ValueError) as exc:
                raise ParamError("price table: %s is malformed" % where) from exc
            per = item.get("per_million")
            if per is not None:
                if not isinstance(per, dict) or sorted(per) != sorted(BUCKETS):
                    raise ParamError("price table: %s.per_million needs the five buckets" % where)
                per = {b: _price(per[b], where + "." + b) for b in BUCKETS}
            self.entries.append({"provider": provider, "model": model, "valid_from": valid_from,
                                 "per_million": per, "inserted": False})
        ce = obj.get("claude_enterprise", {})
        self.seat_per_month = _price(ce.get("seat_per_month"), "claude_enterprise.seat_per_month", 2)
        if ce.get("usage_discount") != "0":
            raise ParamError('price table: claude_enterprise.usage_discount must be "0" in v1')
        gh = obj.get("github_copilot", {})
        if gh.get("credit_price") != "0.01":
            raise ParamError('price table: github_copilot.credit_price must be "0.01"')
        self.credit_price = Decimal("0.01")
        self.copilot_plans = {}
        for name, plan in sorted(gh.get("plans", {}).items()):
            self.copilot_plans[name] = {
                "seat_per_month": _price(plan.get("seat_per_month"), "plans.%s.seat_per_month" % name, 2),
                "included_credits_per_seat": _price(plan.get("included_credits_per_seat"), "plans.%s.included" % name, 4),
            }
        self.credits_per_interaction = {
            model: _price(value, "credits_per_interaction." + model, 1)
            for model, value in sorted(gh.get("credits_per_interaction", {}).items())
        }
        budget = gh.get("budget_usd")
        self.budget_usd = None if budget is None else _price(budget, "budget_usd", 2)
        self._sorted = None

    def _ordered(self):
        if self._sorted is None:
            self._sorted = sorted(
                range(len(self.entries)),
                key=lambda i: (self.entries[i]["valid_from"], self.entries[i]["inserted"], i),
            )
        return [self.entries[i] for i in self._sorted]

    def has_entry(self, provider, model):
        return any(e["provider"] == provider and e["model"] == model for e in self.entries)

    def version(self, provider, model, day):
        """The per-million prices valid on ``day`` (None: unpriced or no entry)."""
        chosen = None
        for entry in self._ordered():
            if entry["provider"] == provider and entry["model"] == model and entry["valid_from"] <= day:
                chosen = entry
        return None if chosen is None else chosen["per_million"]

    def insert_change(self, provider, model, when, factor):
        """Add the price-change version; return (before, after) or None when it cannot apply."""
        before = self.version(provider, model, when)
        if before is None:
            return None
        after = {b: (before[b] * factor).quantize(_PRICE_QUANT, ROUND_HALF_EVEN) for b in BUCKETS}
        self.entries.append({"provider": provider, "model": model, "valid_from": when,
                             "per_million": after, "inserted": True})
        self._sorted = None
        return before, after


def load_price_table(path=None):
    source = Path(path) if path else DATA / "prices.json"
    return PriceTable(load_json_file(source))


def amount(version, uncached, cache_read, write_5m, write_1h, output):
    """The amount of one ledger row, quantized once to eight decimals (8.2)."""
    total = (Decimal(uncached) * version["input_uncached"]
             + Decimal(cache_read) * version["cache_read"]
             + Decimal(write_5m) * version["cache_write_5m"]
             + Decimal(write_1h) * version["cache_write_1h"]
             + Decimal(output) * version["output"]) / MILLION
    return total.quantize(_ROW_QUANT, ROUND_HALF_EVEN)


def per_million_text(version):
    from meterline import amount_str

    return {b: amount_str(version[b]) for b in BUCKETS}
