"""Ground-truth spend for meterline-anthropic-admin (spec v1.0, section 6): spend a known, small amount of API credit with
calls whose usage is recorded exactly, then check that the connector finds the same numbers.

THIS SCRIPT SPENDS REAL MONEY WHEN RUN WITHOUT ``--compare``. It is the owner's to run; no session runs it.

Spend (default mode)
    ANTHROPIC_API_KEY=... python tools/calibrate_anthropic_admin.py [--out-dir DIR]

    The key is a standard API key (sk-ant-api...) of a dedicated workspace that carries its own spend limit, read from
    the environment only and never written anywhere. The run makes the planned calls one after another (see ``plan``),
    writes one line per call to ``calibration-<UTC date>.jsonl`` (model, UTC time, request id, the response's own
    ``usage`` block) and the expected cost per model to ``calibration-<UTC date>-expected.json``. It keeps a running
    cost from each response's ``usage`` and stops BEFORE a call that could take the total past GUARD (2.00 USD). It
    refuses to start at or after 20:00 UTC so that the calls and the Admin API's daily buckets stay inside one UTC day.

Compare (no spend, no key)
    python tools/calibrate_anthropic_admin.py --compare --calibration FILE --data-dir DIR --day YYYY-MM-DD

    Run the connector first for that one day, filtered to the calibration workspace and no earlier than an hour after
    the last call (cost is daily; usage is fresh within minutes):

        ANTHROPIC_ADMIN_KEY=... meterline-anthropic-admin backfill --provider-account-id <label> --workspace-id <workspace> --since DAY

    Tokens must match exactly per model and bucket, cost within 0.01 USD per model. Anything else fails with the
    numbers. The output is figures only, never an id.

Exit codes: 0 pass or complete, 1 compare failed or the run did not complete, 2 configuration error.
"""

import argparse
import json
import os
import random
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, NamedTuple, Optional, Sequence

import httpx

BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
KEY_ENV = "ANTHROPIC_API_KEY"
GUARD = Decimal("2.00")  # USD; the run stops before a call that could pass it
COST_TOLERANCE = Decimal("0.01")  # USD per model, in the compare
LATEST_START_HOUR = 20  # UTC

# USD per million tokens (input, output), from the pricing page of 2026-10-17 as the spec records it, confirmed on the
# page of 2026-10-26. Cache prices are multiples of the input price: a 5-minute write 1.25, a 1-hour write 2, and a read
# that depends on the model (READ below). Confirm the table against the live pricing page before the run; the compare
# tests the connector against THIS table, so a wrong table shows up as a cost difference, not as a wrong pass.
PRICES: Dict[str, tuple] = {
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
    "claude-sonnet-5-5": (Decimal("2"), Decimal("10")),
    "claude-opus-5-5": (Decimal("4"), Decimal("20")),
}
WRITE_5M, WRITE_1H = Decimal("1.25"), Decimal("2")
READ = {  # cache read, as a multiple of the input price; per model on the pricing page of 2026-10-26
    "claude-haiku-5-5": Decimal("0.1"),
    "claude-sonnet-5-5": Decimal("0.05"),
    "claude-opus-5-5": Decimal("0.05"),
}
PER_MILLION = Decimal(1000000)
CHARS_PER_TOKEN = 3  # a prompt of this many characters is assumed to be at most one token per three: an over-estimate

TOKEN_FIELDS = (  # compare name -> (record field, how to read it from a usage block)
    ("tokens_input_uncached", lambda u: u["input_tokens"]),
    ("tokens_cache_read", lambda u: u["cache_read_input_tokens"]),
    ("tokens_cache_write_5m", lambda u: u["cache_creation"]["ephemeral_5m_input_tokens"]),
    ("tokens_cache_write_1h", lambda u: u["cache_creation"]["ephemeral_1h_input_tokens"]),
    ("tokens_output", lambda u: u["output_tokens"]),
)


class Call(NamedTuple):
    model: str
    kind: str  # "plain", "write_5m", "read_5m", "write_1h" or "read_1h"
    prompt_tokens: int  # about; the response's usage is the truth
    max_tokens: int
    group: int  # calls of one group share their prompt (the cache calls)


class Result(NamedTuple):
    status: str  # "complete", "stopped" (the guard) or "failed" (an API error)
    spent: Decimal
    calls: int
    path: Path
    message: str = ""


def plan() -> List[Call]:
    """The planned calls (spec section 6): Haiku 20 plain calls of about 50k input tokens plus two cache pairs (a
    5-minute write then a read, a 1-hour write then a read); Sonnet 20 calls with about 2k input and up to 3k output;
    Opus 5 calls with about 2k input and up to 4k output."""
    haiku, sonnet, opus = list(PRICES)
    calls = [Call(haiku, "plain", 50000, 100, 0) for _ in range(20)]
    for group, (write, read) in enumerate((("write_5m", "read_5m"), ("write_1h", "read_1h")), start=1):
        calls += [Call(haiku, write, 10000, 100, group), Call(haiku, read, 10000, 100, group)]
    calls += [Call(sonnet, "plain", 2000, 3000, 0) for _ in range(20)]
    calls += [Call(opus, "plain", 2000, 4000, 0) for _ in range(5)]
    return calls


def expected_cost(model: str, usage: Mapping[str, Any]) -> Decimal:
    """USD for one response's ``usage`` block under the price table."""
    price_in, price_out = PRICES[model]
    creation = usage.get("cache_creation") or {}
    five = creation.get("ephemeral_5m_input_tokens", 0)
    hour = creation.get("ephemeral_1h_input_tokens", 0)
    units = (Decimal(usage["input_tokens"]) + Decimal(five) * WRITE_5M + Decimal(hour) * WRITE_1H
             + Decimal(usage.get("cache_read_input_tokens", 0)) * READ[model])
    return (units * price_in + Decimal(usage["output_tokens"]) * price_out) / PER_MILLION


def worst_case(call: Call, history: Mapping[str, Decimal]) -> Decimal:
    """The most the next call could cost: its own estimate, or the dearest earlier call of the same model, whichever is
    more. The first call of a model has only its estimate, so the workspace spend limit stays the hard stop."""
    price_in, price_out = PRICES[call.model]
    multiplier = WRITE_1H if call.kind == "write_1h" else WRITE_5M if call.kind == "write_5m" else Decimal(1)
    estimate = (Decimal(call.prompt_tokens) * multiplier * price_in + Decimal(call.max_tokens) * price_out) / PER_MILLION
    return max(estimate, history.get(call.model, Decimal(0)))


def _prompt(call: Call, serial: int, rng: random.Random) -> str:
    """Filler text of about ``call.prompt_tokens`` tokens. Plain calls start with a unique line so that no prefix is
    cached by accident; the calls of one cache group share the whole prompt."""
    words = ["alpha", "bridge", "copper", "delta", "ember", "fjord", "garnet", "harbor", "island", "juniper", "kettle", "lantern"]
    seed = random.Random(call.group if call.group else serial)
    body = " ".join(seed.choice(words) for _ in range(int(call.prompt_tokens * 0.75)))
    head = "Reference text %d.\n" % (call.group if call.group else rng.randrange(10 ** 9))
    if call.model == "claude-haiku-5-5":
        return head + body + "\nReply with the single word OK."
    return head + body + "\nWrite a story of about %d words about a bridge." % (call.max_tokens // 2)


def _payload(call: Call, text: str) -> Dict[str, Any]:
    block: Dict[str, Any] = {"type": "text", "text": text}
    if call.kind in ("write_5m", "read_5m"):
        block["cache_control"] = {"type": "ephemeral"}
    elif call.kind in ("write_1h", "read_1h"):
        block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    return {"model": call.model, "max_tokens": call.max_tokens, "messages": [{"role": "user", "content": [block]}]}


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_calibration(
    transport: Optional[httpx.BaseTransport], out_dir: Path, *, now: datetime, api_key: str, guard: Decimal = GUARD,
    clock: Optional[Callable[[], datetime]] = None,
) -> Result:
    """Make the planned calls, keep each response's ``usage``, stop before the guard. ``transport`` is None only in the
    real run; every test passes a mock."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    day = now.astimezone(timezone.utc).date().isoformat()
    path = out_dir / ("calibration-%s.jsonl" % day)
    spent = Decimal(0)
    history: Dict[str, Decimal] = {}
    per_model: Dict[str, Decimal] = {}
    done = 0
    rng = random.Random(now.timestamp())
    status, message = "complete", ""
    headers = {"x-api-key": api_key, "anthropic-version": API_VERSION}
    with httpx.Client(base_url=BASE_URL, headers=headers, transport=transport, timeout=300) as client, open(path, "w", encoding="utf-8") as sink:
        for serial, call in enumerate(plan()):
            if spent + worst_case(call, history) > guard:
                status, message = "stopped", "stopped before call %d: it could take the total past %s USD" % (serial + 1, guard)
                break
            response = client.post("/v1/messages", json=_payload(call, _prompt(call, serial, rng)))
            if response.status_code != 200:
                status, message = "failed", "call %d: HTTP %d" % (serial + 1, response.status_code)  # the body is not echoed: it could carry the key
                break
            usage = response.json()["usage"]
            cost = expected_cost(call.model, usage)
            spent += cost
            per_model[call.model] = per_model.get(call.model, Decimal(0)) + cost
            history[call.model] = max(history.get(call.model, Decimal(0)), cost)
            done += 1
            sink.write(json.dumps({"model": call.model, "time": _stamp(clock() if clock else now),
                                   "request_id": response.headers.get("request-id", ""), "usage": usage}, sort_keys=True) + "\n")
            sink.flush()
    expected = out_dir / ("calibration-%s-expected.json" % day)
    expected.write_text(json.dumps({m: format(c, "f") for m, c in sorted(per_model.items())}, indent=2) + "\n", encoding="utf-8")
    return Result(status, spent, done, path, message)


# ---- compare ----------------------------------------------------------------------------------------------------------


def _truth(path: Path) -> Dict[str, Dict[str, Any]]:
    """Per model: the token sums of the calibration file and the cost the price table gives for its calls."""
    out: Dict[str, Dict[str, Any]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        call = json.loads(line)
        mine = out.setdefault(call["model"], {"cost": Decimal(0), **{name: 0 for name, _ in TOKEN_FIELDS}})
        if call["model"] in PRICES:
            mine["cost"] += expected_cost(call["model"], call["usage"])
        else:
            mine["cost"] = None  # no price in the table: the compare says so instead of guessing
        for name, read in TOKEN_FIELDS:
            mine[name] += read(call["usage"])
    return out


def _found(data_dir: Path, day: str) -> Dict[str, Dict[str, Any]]:
    """Per model: what the connector's organisation totals hold for the day (latest batch of each key)."""
    from meterline import KINDS, latest_per_key, read_jsonl

    records = []
    folder = Path(data_dir) / "records" / "org_totals"
    for file in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        records.extend(read_jsonl(file, KINDS["org_totals"]))
    out: Dict[str, Dict[str, Any]] = {}
    for rec in latest_per_key(records).values():
        if rec.period_start.isoformat() == day and rec.product == "api":
            out[rec.model] = {"cost": rec.cost, **{name: getattr(rec, name) for name, _ in TOKEN_FIELDS}}
    return out


def compare(calibration: Path, data_dir: Path, day: str) -> List[str]:
    """Differences between the calibration file and the connector's records for ``day``; an empty list is a pass."""
    truth, found = _truth(calibration), _found(data_dir, day)
    failures: List[str] = []
    for model in sorted(set(truth) | set(found)):
        if model not in found:
            failures.append("%s: in the calibration, not in the connector's records" % model)
            continue
        if model not in truth:
            failures.append("%s: in the connector's records, not in the calibration" % model)
            continue
        for name, _read in TOKEN_FIELDS:
            if truth[model][name] != found[model][name]:
                failures.append("%s: %s expected %s, found %s" % (model, name, truth[model][name], found[model][name]))
        cost = found[model]["cost"]
        if truth[model]["cost"] is None:
            failures.append("%s: no price in the table, cost not checked" % model)
        elif cost is None or abs(cost - truth[model]["cost"]) > COST_TOLERANCE:
            failures.append("%s: cost expected %s USD, found %s USD (tolerance %s)" % (model, format(truth[model]["cost"], "f"), cost, COST_TOLERANCE))
    return failures


# ---- entry point --------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--compare", action="store_true", help="compare the calibration file with the connector's records (no spend)")
    parser.add_argument("--out-dir", default="exports/meterline-calibration", help="where the calibration files are written (gitignored)")
    parser.add_argument("--calibration", help="--compare: the calibration-<date>.jsonl file")
    parser.add_argument("--data-dir", help="--compare: the connector's data directory")
    parser.add_argument("--day", help="--compare: the UTC day, YYYY-MM-DD")
    parser.add_argument("--guard", type=Decimal, default=GUARD, help="USD at which the run stops (default 2.00)")
    return parser


def _compare_main(args: argparse.Namespace) -> int:
    if not (args.calibration and args.data_dir and args.day):
        print("--compare needs --calibration, --data-dir and --day", file=sys.stderr)
        return 2
    try:
        date.fromisoformat(args.day)
        failures = compare(Path(args.calibration), Path(args.data_dir), args.day)
    except (OSError, ValueError, KeyError) as exc:
        print("compare could not run: %s" % type(exc).__name__, file=sys.stderr)
        return 1
    if not failures and not _found(Path(args.data_dir), args.day):
        failures = ["the connector's data directory holds no organisation totals for %s" % args.day]
    for line in failures:
        print("FAIL " + line)
    print("%s: %d difference(s) for %s" % ("PASS" if not failures else "FAIL", len(failures), args.day))
    return 0 if not failures else 1


def main(argv: Optional[Sequence[str]] = None, *, transport: Optional[httpx.BaseTransport] = None,
         now: Optional[Callable[[], datetime]] = None, environ: Optional[Mapping[str, str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.compare:
        return _compare_main(args)
    key = (os.environ if environ is None else environ).get(KEY_ENV, "")
    if not key:
        print("%s must be set in the environment (a standard API key of the calibration workspace)" % KEY_ENV, file=sys.stderr)
        return 2
    moment = (now or (lambda: datetime.now(timezone.utc)))()
    if moment.astimezone(timezone.utc).hour >= LATEST_START_HOUR:
        print("refusing to start at or after %02d:00 UTC: the calls and the daily buckets must stay inside one UTC day" % LATEST_START_HOUR,
              file=sys.stderr)
        return 2
    result = run_calibration(transport, Path(args.out_dir), now=moment, api_key=key, guard=args.guard,
                             clock=(lambda: datetime.now(timezone.utc)) if now is None else None)
    print("%s: %d calls, %s USD spent (guard %s USD). %s" % (result.status, result.calls, format(result.spent, "f"), args.guard, result.message))
    print("calibration file: %s" % result.path)
    return 0 if result.status == "complete" else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
