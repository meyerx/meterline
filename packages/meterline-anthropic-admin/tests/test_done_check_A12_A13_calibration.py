"""A12 and A13 (spec sections 6 and 8): the calibration compare passes on matching fixtures and fails on one token off;
the calibration stops before its 2.00 USD guard on a fake API. Nothing here spends money or opens a connection."""

import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from admin_helpers import backfill
from meterline_anthropic_admin.testing import crow, urow, utc

SCRIPT = Path(__file__).resolve().parents[3] / "tools" / "calibrate_anthropic_admin.py"
DAY = "2026-09-15"
KEY = "sk" + "-ant-api03-synthetic-not-a-real-key"
HAIKU, SONNET, OPUS = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"
WORKSPACE = "wrkspc_cal"


@pytest.fixture(scope="module")
def cal():
    spec = importlib.util.spec_from_file_location("calibrate_anthropic_admin", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["calibrate_anthropic_admin"] = module
    spec.loader.exec_module(module)
    return module


def usage_block(inp=0, out=0, read=0, c5=0, c1=0):
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": c5 + c1, "cache_creation": {"ephemeral_5m_input_tokens": c5, "ephemeral_1h_input_tokens": c1}}


CALLS = [
    {"model": HAIKU, "time": DAY + "T10:00:00Z", "request_id": "req_a1", "usage": usage_block(inp=50000, out=7)},
    {"model": HAIKU, "time": DAY + "T10:01:00Z", "request_id": "req_a2", "usage": usage_block(inp=12, out=5, c5=9000)},
    {"model": HAIKU, "time": DAY + "T10:02:00Z", "request_id": "req_a3", "usage": usage_block(inp=12, out=5, read=9000)},
    {"model": HAIKU, "time": DAY + "T10:03:00Z", "request_id": "req_a4", "usage": usage_block(inp=12, out=5, c1=9000)},
    {"model": SONNET, "time": DAY + "T10:04:00Z", "request_id": "req_b1", "usage": usage_block(inp=2100, out=2900)},
    {"model": OPUS, "time": DAY + "T10:05:00Z", "request_id": "req_c1", "usage": usage_block(inp=2000, out=3800)},
]


def write_calibration(path, calls=CALLS):
    path.write_text("".join(json.dumps(c) + "\n" for c in calls), encoding="utf-8")
    return path


def fixtures_for(cal, calls, *, extra_tokens=0, extra_cents=Decimal(0)):
    """What a correct Admin API would show for these calls: summed tokens, and the cost the price table gives, in cents."""
    usage, cost = [], []
    for model in sorted({c["model"] for c in calls}):
        mine = [c["usage"] for c in calls if c["model"] == model]
        total = lambda name: sum(u["cache_creation"][name] if name.startswith("ephemeral") else u[name] for u in mine)  # noqa: E731
        usage.append(urow(DAY, "apikey_cal", model, ws=WORKSPACE, uncached=total("input_tokens") + extra_tokens,
                          c5=total("ephemeral_5m_input_tokens"), c1=total("ephemeral_1h_input_tokens"),
                          read=total("cache_read_input_tokens"), out=total("output_tokens")))
        dollars = sum(cal.expected_cost(model, u) for u in mine)
        cost.append(crow(DAY, model, "uncached_input_tokens", format(dollars * 100 + extra_cents, "f"), ws=WORKSPACE))
    return usage, cost


def pull(cal, make_api, fake_clock, data_dir, **kw):
    usage, cost = fixtures_for(cal, CALLS, **kw)
    assert backfill(data_dir, make_api(usage, cost), fake_clock, DAY, utc("2026-09-18T06:00:00"), "--workspace-id", WORKSPACE) == 0


def test_done_check_A12_compare_passes_when_connector_and_calibration_agree(cal, make_api, fake_clock, data_dir, tmp_path):
    pull(cal, make_api, fake_clock, data_dir)
    path = write_calibration(tmp_path / "calibration.jsonl")
    assert cal.compare(path, data_dir, DAY) == []


def test_done_check_A12_compare_fails_when_one_token_is_off_and_gives_the_numbers(cal, make_api, fake_clock, data_dir, tmp_path):
    pull(cal, make_api, fake_clock, data_dir, extra_tokens=1)
    failures = cal.compare(write_calibration(tmp_path / "calibration.jsonl"), data_dir, DAY)
    assert failures, "one token off must fail"
    assert any(HAIKU in f and "tokens_input_uncached" in f and "50036" in f and "50037" in f for f in failures), failures


def test_done_check_A12_cost_must_agree_within_one_cent_per_model(cal, make_api, fake_clock, data_dir, tmp_path):
    path = write_calibration(tmp_path / "calibration.jsonl")
    pull(cal, make_api, fake_clock, data_dir, extra_cents=Decimal("0.5"))  # half a cent: inside 0.01 USD
    assert cal.compare(path, data_dir, DAY) == []
    other = data_dir.parent / "two"
    other.mkdir()
    pull(cal, make_api, fake_clock, other, extra_cents=Decimal("2"))  # two cents: outside
    failures = cal.compare(path, other, DAY)
    assert failures and all("cost" in f for f in failures)


def test_done_check_A12_a_model_on_one_side_only_is_a_difference(cal, make_api, fake_clock, data_dir, tmp_path):
    pull(cal, make_api, fake_clock, data_dir)
    fewer = write_calibration(tmp_path / "fewer.jsonl", [c for c in CALLS if c["model"] != OPUS])
    assert any(OPUS in f for f in cal.compare(fewer, data_dir, DAY))
    more = write_calibration(tmp_path / "more.jsonl", CALLS + [dict(CALLS[0], model="claude-other-1", request_id="req_x")])
    assert any("claude-other-1" in f for f in cal.compare(more, data_dir, DAY))


def test_done_check_A12_the_command_line_compare_prints_figures_only(cal, make_api, fake_clock, data_dir, tmp_path, capsys):
    pull(cal, make_api, fake_clock, data_dir)
    path = write_calibration(tmp_path / "calibration.jsonl")
    assert cal.main(["--compare", "--calibration", str(path), "--data-dir", str(data_dir), "--day", DAY]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "req_" not in out and "apikey_cal" not in out and WORKSPACE not in out
    assert cal.main(["--compare", "--calibration", str(path), "--data-dir", str(data_dir / "missing"), "--day", DAY]) == 1


def test_done_check_A12_expected_cost_follows_the_price_table(cal):
    assert cal.PRICES[HAIKU] == (Decimal("0.10"), Decimal("0.50"))
    assert cal.PRICES[SONNET] == (Decimal("2"), Decimal("10")) and cal.PRICES[OPUS] == (Decimal("4"), Decimal("20"))
    assert cal.expected_cost(HAIKU, usage_block(inp=1_000_000, out=1_000_000)) == Decimal("0.60")
    assert cal.expected_cost(OPUS, usage_block(inp=2000, out=4000)) == Decimal("0.088")
    assert type(cal.expected_cost(SONNET, usage_block(inp=1))) is Decimal


def test_done_check_A12_cache_read_follows_the_page_per_model(cal):
    # Pricing page read 2026-10-26: a cache read is 0.1 x input on Haiku 5.5, 0.05 x on Sonnet 5.5 and Opus 5.5.
    assert cal.expected_cost(HAIKU, usage_block(read=1_000_000)) == Decimal("0.01")
    assert cal.expected_cost(SONNET, usage_block(read=1_000_000)) == Decimal("0.10")
    assert cal.expected_cost(OPUS, usage_block(read=1_000_000)) == Decimal("0.20")


def test_done_check_A12_haiku_calls_stay_in_the_lower_price_tier(cal):
    # Haiku 5.5 bills a prompt over 100,000 tokens (cache reads and writes included) at higher prices,
    # which the price table does not carry.
    assert all(call.prompt_tokens < 100_000 for call in cal.plan() if call.model == HAIKU)


# ---- A13 ----------------------------------------------------------------------------------------------------------


def fake_messages(usage, sent):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append((request.headers, body))
        return httpx.Response(200, json={"id": "msg_%d" % len(sent), "type": "message", "model": body["model"],
                                         "content": [{"type": "text", "text": "OK"}], "usage": usage(body)},
                              headers={"request-id": "req_%d" % len(sent)})

    return httpx.MockTransport(handler)


def small(body):
    return usage_block(inp=300, out=5)


def heavy(body):
    return usage_block(inp=300_000, out=5)


def run_cal(cal, transport, out, **kw):
    return cal.run_calibration(transport, out, now=utc(DAY + "T10:00:00"), api_key=KEY, **kw)


def test_done_check_A13_a_light_run_makes_every_planned_call_and_keeps_the_usage(cal, tmp_path):
    sent = []
    result = run_cal(cal, fake_messages(small, sent), tmp_path)
    plan = cal.plan()
    assert result.status == "complete" and len(sent) == len(plan) == result.calls
    assert [c.model for c in plan].count(HAIKU) == 24 and [c.model for c in plan].count(SONNET) == 20 and [c.model for c in plan].count(OPUS) == 5
    lines = [json.loads(x) for x in result.path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == len(plan) and lines[0]["usage"]["input_tokens"] == 300
    assert set(lines[0]) == {"model", "time", "request_id", "usage"} and lines[0]["time"] == DAY + "T10:00:00Z"
    assert result.path.name == "calibration-%s.jsonl" % DAY
    expected = json.loads((tmp_path / ("calibration-%s-expected.json" % DAY)).read_text(encoding="utf-8"))
    assert set(expected) == {HAIKU, SONNET, OPUS} and Decimal(expected[HAIKU]) > 0
    for headers, body in sent:
        assert headers["x-api-key"] == KEY and headers["anthropic-version"] == "2023-06-01"
        assert body["max_tokens"] >= 1 and body["messages"][0]["role"] == "user"
    assert KEY not in "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())


def test_done_check_A13_the_guard_stops_the_run_before_2_usd_is_spent(cal, tmp_path):
    sent = []
    result = run_cal(cal, fake_messages(heavy, sent), tmp_path)
    assert cal.GUARD == Decimal("2.00")
    assert result.status == "stopped" and 0 < len(sent) < len(cal.plan())
    assert result.spent <= Decimal("2.00")  # stopped before, not after
    assert result.spent == sum(cal.expected_cost(c["model"], c["usage"]) for c in map(json.loads, result.path.read_text(encoding="utf-8").splitlines()))
    assert len(result.path.read_text(encoding="utf-8").splitlines()) == len(sent)  # what was spent is on record


def test_done_check_A13_a_lower_guard_stops_sooner(cal, tmp_path):
    sent = []
    result = run_cal(cal, fake_messages(small, sent), tmp_path, guard=Decimal("0.0002"))
    assert result.status == "stopped" and result.spent <= Decimal("0.0002") and len(sent) < len(cal.plan())


def test_done_check_A13_an_api_error_stops_the_run_and_keeps_what_was_done(cal, tmp_path):
    seen = []

    def handler(request):
        seen.append(1)
        if len(seen) == 3:
            return httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "echo " + KEY}})
        return httpx.Response(200, json={"id": "m", "type": "message", "content": [], "usage": small(None)}, headers={"request-id": "req_%d" % len(seen)})

    result = run_cal(cal, httpx.MockTransport(handler), tmp_path)
    assert result.status == "failed" and result.calls == 2
    assert len(result.path.read_text(encoding="utf-8").splitlines()) == 2
    assert KEY not in result.message


def test_done_check_A13_the_command_needs_the_key_in_the_environment_and_starts_before_20_utc(cal, tmp_path, capsys):
    base = ["--out-dir", str(tmp_path)]
    assert cal.main(base, environ={}, now=lambda: utc(DAY + "T10:00:00")) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    sent = []
    assert cal.main(base, environ={"ANTHROPIC_API_KEY": KEY}, transport=fake_messages(small, sent), now=lambda: utc(DAY + "T20:00:00")) == 2
    assert sent == [] and "20:00" in capsys.readouterr().err
    assert cal.main(base, environ={"ANTHROPIC_API_KEY": KEY}, transport=fake_messages(small, sent), now=lambda: utc(DAY + "T19:59:00")) == 0
    out = capsys.readouterr().out
    assert KEY not in out and "complete" in out and len(sent) == len(cal.plan())
