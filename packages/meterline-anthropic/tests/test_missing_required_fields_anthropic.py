"""F-06: a required measure field that is missing or mistyped in a provider
response fails the run (exit 2, naming the endpoint and the field) and
publishes nothing. It is never counted as zero.

Each case strips one field from every row of one endpoint, in the response the
fake API sends, so the case covers the raw-totals reader and the normaliser.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from meterline_anthropic.cli import main

NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)

USAGE_FIELDS = (
    "uncached_input_tokens", "output_tokens", "cache_read_input_tokens",
    "cache_creation.ephemeral_5m_input_tokens", "cache_creation.ephemeral_1h_input_tokens",
    "server_tool_use.web_search_requests",
)
# (endpoint, fields): every field here is a required key of a row of the endpoint
CASES = (
    [("user_usage_report", f) for f in USAGE_FIELDS + ("actor.user_id", "starting_at")]
    + [("usage_report", f) for f in USAGE_FIELDS]
    + [("user_cost_report", f) for f in ("amount", "list_amount", "actor.user_id", "starting_at")]
    + [("cost_report", f) for f in ("amount", "list_amount")]
    + [("summaries", f) for f in (
        "assigned_seat_count", "daily_active_user_count", "weekly_active_user_count", "monthly_active_user_count",
        "starting_at")]
    + [("users", "user.id")]
)
REPORT_ROWS = {
    "user_usage_report": lambda body: body["data"],
    "user_cost_report": lambda body: body["data"],
    "usage_report": lambda body: [r for bucket in body["data"] for r in bucket["results"]],
    "cost_report": lambda body: [r for bucket in body["data"] for r in bucket["results"]],
    "summaries": lambda body: body["data"],
    "users": lambda body: body["data"],
}


def _delete(row, path):
    *parents, leaf = path.split(".")
    node = row
    for name in parents:
        node = node.get(name)
        if not isinstance(node, dict):
            return
    node.pop(leaf, None)


def _set(row, path, value):
    *parents, leaf = path.split(".")
    node = row
    for name in parents:
        node = node[name]
    node[leaf] = value


def _changing(fake_api, endpoint, edit):
    """A transport that applies ``edit(row)`` to every row of ``endpoint``."""

    def handler(request: httpx.Request) -> httpx.Response:
        response = fake_api.transport.handle_request(request)
        if response.status_code != 200 or not request.url.path.endswith("/" + endpoint):
            return response
        body = json.loads(response.read().decode("utf-8"))
        for row in REPORT_ROWS[endpoint](body):
            edit(row)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _backfill(transport, clock, data_dir):
    return main(
        ["backfill", "--since", "2026-10-01", "--data-dir", str(data_dir)],
        transport=transport, clock=clock, sleep=clock.sleep, now=lambda: NOW, jitter=lambda: 0,
    )


def _no_records(data_dir):
    return not list(Path(data_dir).glob("records/*/*.jsonl"))


def _failed_manifests(data_dir):
    found = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((Path(data_dir) / "runs").glob("*.json"))]
    return [m for m in found if m["status"] == "failed"]


@pytest.mark.parametrize("endpoint,path", CASES, ids=["%s-%s" % c for c in CASES])
def test_missing_required_field_anthropic(endpoint, path, fake_api, fake_clock, tmp_path, capsys):
    transport = _changing(fake_api, endpoint, lambda row: _delete(row, path))
    rc = _backfill(transport, fake_clock, tmp_path)
    err = capsys.readouterr().err
    assert rc == 2
    assert endpoint in err
    assert path.split(".")[-1] in err
    assert "Traceback" not in err
    assert _no_records(tmp_path)
    assert _failed_manifests(tmp_path)


@pytest.mark.parametrize(
    "endpoint,path,value",
    [
        ("user_usage_report", "output_tokens", "12"),
        ("user_usage_report", "cache_creation.ephemeral_5m_input_tokens", 1.5),
        ("user_usage_report", "uncached_input_tokens", None),
        ("user_usage_report", "uncached_input_tokens", True),
        ("user_usage_report", "actor.user_id", None),
        ("usage_report", "output_tokens", "12"),
        ("user_cost_report", "amount", 12),
        ("user_cost_report", "list_amount", 12.5),
        ("cost_report", "amount", 12),
        ("cost_report", "list_amount", 12.5),
        ("user_cost_report", "amount", "NaN"),
        ("user_cost_report", "list_amount", "Infinity"),
        ("user_cost_report", "amount", None),
        ("user_cost_report", "amount", "lots"),
        ("cost_report", "amount", "-Infinity"),
        ("summaries", "daily_active_user_count", "31"),
        ("users", "user.id", None),
    ],
)
def test_missing_required_field_anthropic_wrong_type(endpoint, path, value, fake_api, fake_clock, tmp_path, capsys):
    transport = _changing(fake_api, endpoint, lambda row: _set(row, path, value))
    rc = _backfill(transport, fake_clock, tmp_path)
    err = capsys.readouterr().err
    assert rc == 2
    assert endpoint in err
    assert path.split(".")[-1] in err
    assert "Traceback" not in err
    assert _no_records(tmp_path)


def test_missing_required_field_anthropic_raw_totals_reader_is_strict():
    """The totals read from the raw rows use the same strict path, so the
    records-equal-raw-totals check cannot agree with a silent zero."""
    from meterline_anthropic.fetch import _report_totals
    from meterline_anthropic.normalise import ShapeError

    row = {"output_tokens": 1, "cache_read_input_tokens": 1, "cache_creation": {
        "ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 1}, "server_tool_use": {"web_search_requests": 0}}
    with pytest.raises(ShapeError, match="uncached_input_tokens"):
        _report_totals([row], "usage", "usage_report")
    with pytest.raises(ShapeError, match="list_amount"):
        _report_totals([{"amount": "1.00"}], "cost", "cost_report")


def test_optional_fields_may_be_absent_or_null(fake_api, fake_clock, tmp_path):
    """requests is int|null and a seat count may be null (spec 2.1); neither fails the run."""
    transport = _changing(fake_api, "user_usage_report", lambda row: row.pop("requests", None))
    assert _backfill(transport, fake_clock, tmp_path) == 0

    other = tmp_path / "second"
    transport = _changing(fake_api, "summaries", lambda row: row.update(assigned_seat_count=None, pending_invite_count=None))
    assert _backfill(transport, fake_clock, other) == 0
    seats = [json.loads(line) for p in (other / "records" / "seats").glob("*.jsonl") for line in p.read_text().splitlines()]
    assert seats and all(r["seats"] == 0 and r["pending_invites"] is None for r in seats)


def test_rebuild_over_a_damaged_raw_file_exits_2_and_leaves_records(fake_api, fake_clock, tmp_path, capsys):
    import gzip

    assert _backfill(fake_api.transport, fake_clock, tmp_path) == 0
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in (tmp_path / "records").rglob("*.jsonl")}
    assert before

    def null_output_tokens(node):
        if isinstance(node, dict):
            if "output_tokens" in node:
                node["output_tokens"] = None
            for value in node.values():
                null_output_tokens(value)
        elif isinstance(node, list):
            for value in node:
                null_output_tokens(value)

    for raw in sorted((tmp_path / "raw").rglob("*usage_report/*/*.json.gz")):
        envelope = json.loads(gzip.decompress(raw.read_bytes()).decode("utf-8"))
        for page in envelope["pages"]:
            body = json.loads(page["body"])
            null_output_tokens(body)
            page["body"] = json.dumps(body)
        raw.write_bytes(gzip.compress(json.dumps(envelope).encode("utf-8")))
    capsys.readouterr()

    assert main(["rebuild", "--data-dir", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "rebuild:" in err and "20261005T060000Z" in err and "output_tokens" in err
    assert "Traceback" not in err and len(err.strip().splitlines()) == 1
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in (tmp_path / "records").rglob("*.jsonl")}
    assert after == before
    assert not (tmp_path / ".rebuild").exists()
