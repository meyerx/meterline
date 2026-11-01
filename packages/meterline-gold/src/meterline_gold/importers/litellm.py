"""Offline LiteLLM price importer (spec 3.4). Reads a local copy of LiteLLM's public price file; no network."""

import json
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import yaml

from meterline_gold.errors import InputError

MILLION = Decimal(1000000)
SIX = Decimal("0.000001")


def _per_million(value):
    return str((value * MILLION).quantize(SIX, rounding=ROUND_HALF_EVEN))


def read_keys(path):
    keys = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            keys.append(line)
    return keys


def convert(file, keys_file, as_of):
    """The price list object (a dict) for the keys of ``keys_file`` found in LiteLLM's file ``file``."""
    try:
        text = Path(file).read_text(encoding="utf-8")
        data = json.loads(text, parse_float=Decimal, parse_int=Decimal)
    except (OSError, ValueError) as exc:
        raise InputError("%s: not a readable JSON file (%s)" % (file, exc)) from exc
    if not isinstance(data, dict):
        raise InputError("%s: expected a JSON object" % file)
    keys = read_keys(keys_file)
    models, prices, seen = [], [], {}
    for key in keys:
        if key not in data or not isinstance(data[key], dict):
            raise InputError("%s: key %r is not in %s" % (keys_file, key, file))
        entry = data[key]
        model = key.rsplit("/", 1)[-1]
        if model in seen:
            raise InputError("keys %r and %r give the same model %r" % (seen[model], key, model))
        seen[model] = key
        inp = entry.get("input_cost_per_token")
        out = entry.get("output_cost_per_token")
        cache_read = entry.get("cache_read_input_token_cost")
        write5 = entry.get("cache_creation_input_token_cost")
        write1 = entry.get("cache_creation_input_token_cost_above_1hr")
        missing = [name for name, value in (("input_cost_per_token", inp), ("output_cost_per_token", out)) if value is None]
        if missing:
            raise InputError(
                "model %r (key %r in %s) has no %s; a listed model needs an input and an output price"
                % (model, key, file, " and no ".join(missing))
            )
        per_million = {
            "input_uncached": _per_million(inp),
            "cache_read": _per_million(cache_read if cache_read is not None else inp),
            "cache_write_5m": _per_million(write5 if write5 is not None else inp),
            "cache_write_1h": None if write1 is None else _per_million(write1),
            "output": _per_million(out),
        }
        models.append({"model": model, "vendor": str(entry.get("litellm_provider", "")), "family": "", "tier": "unclassified"})
        prices.append({"provider": "*", "model": model, "valid_from": as_of, "per_million": per_million})
    models.sort(key=lambda m: m["model"])
    prices.sort(key=lambda p: p["model"])
    return {
        "price_list_format": 1,
        "as_of": as_of,
        "source": "LiteLLM model_prices_and_context_window.json, local copy, keys listed in %s" % Path(keys_file).name,
        "currency": "USD",
        "models": models,
        "token_prices": prices,
        "unit_prices": [],
        "included": [],
        "seats": [],
    }


def write(obj, out):
    text = yaml.safe_dump(obj, sort_keys=True, default_flow_style=False, allow_unicode=True)
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
