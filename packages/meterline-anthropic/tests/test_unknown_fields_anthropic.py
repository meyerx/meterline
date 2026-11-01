"""A59: a field the provider sent and the connector does not know is reported, not fatal.

The run publishes as usual and ends with the status it would have had; the manifest lists the field under
``unknown_fields`` (endpoint, key path, count, never a value), one warning goes to stderr, and ``RunResult`` carries the
same list. A required field that is missing or mistyped still fails the run (test_missing_required_fields_anthropic.py).
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from meterline_anthropic import run
from meterline_anthropic.cli import main
from meterline_anthropic.testing import FakeAPI

PLANTED = "value-that-must-never-be-printed"
NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
SINCE = date(2026, 9, 1)
KEY_ENV = "ANTHROPIC_ANALYTICS_KEY"

# endpoint -> (the rows of a body, where the planted key goes: a path inside a row)
ROWS = {
    "user_usage_report": lambda body: body["data"],
    "user_cost_report": lambda body: body["data"],
    "usage_report": lambda body: [r for bucket in body["data"] for r in bucket["results"]],
    "cost_report": lambda body: [r for bucket in body["data"] for r in bucket["results"]],
    "summaries": lambda body: body["data"],
    "users": lambda body: body["data"],
}


def _planting(fake, endpoint, edit):
    """A transport that applies ``edit(row)`` to every row of ``endpoint`` in the fake's answers."""

    def handler(request: httpx.Request) -> httpx.Response:
        response = fake.transport.handle_request(request)
        if response.status_code != 200 or not request.url.path.endswith("/" + endpoint):
            return response
        body = json.loads(response.read().decode("utf-8"))
        for row in ROWS[endpoint](body):
            edit(row)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _backfill(tmp_path, transport):
    return run("backfill", since=SINCE, data_dir=tmp_path, transport=transport, now=lambda: NOW, credentials={KEY_ENV: "right"})


def _manifest(result):
    return json.loads(result.manifest_path.read_text(encoding="utf-8"))


def _warnings(err):
    return [line for line in err.splitlines() if line.startswith("warning:")]


@pytest.mark.parametrize("endpoint", sorted(ROWS))
def test_unknown_field_is_listed_in_the_manifest_and_the_run_still_publishes(endpoint, tmp_path, capsys):
    fake = FakeAPI(api_key="right")
    result = _backfill(tmp_path, _planting(fake, endpoint, lambda row: row.update(surprise_field=PLANTED)))
    err = capsys.readouterr().err
    assert result.exit_code == 0 and result.ok and sum(result.records.values()) > 0
    manifest = _manifest(result)
    assert manifest["status"] == "published"
    listed = [u for u in manifest["unknown_fields"] if u["endpoint"] == endpoint]
    assert len(listed) == 1 and listed[0]["path"].endswith("surprise_field") and listed[0]["count"] > 0
    assert result.unknown_fields == tuple(manifest["unknown_fields"])
    assert len(_warnings(err)) == 1 and endpoint in _warnings(err)[0]
    assert PLANTED not in err and PLANTED not in json.dumps(manifest)


def test_unknown_field_inside_a_known_object_is_listed_with_its_full_key_path(tmp_path):
    fake = FakeAPI(api_key="right")

    def plant(row):
        row["cache_creation"]["ephemeral_24h_input_tokens"] = 5

    result = _backfill(tmp_path, _planting(fake, "user_usage_report", plant))
    assert result.ok
    paths = {u["path"] for u in result.unknown_fields}
    assert paths == {"data[].cache_creation.ephemeral_24h_input_tokens"}


def test_unknown_field_in_a_data_keyed_map_names_the_wildcard_not_the_key(tmp_path):
    fake = FakeAPI(api_key="right")

    def plant(row):
        tools = (row.get("claude_code_metrics") or {}).get("tool_actions")
        if isinstance(tools, dict):
            for name in list(tools):
                tools[name]["brand_new_count"] = 1

    result = _backfill(tmp_path, _planting(fake, "users", plant))
    assert result.ok
    paths = {u["path"] for u in result.unknown_fields}
    assert paths == {"data[].claude_code_metrics.tool_actions.*.brand_new_count"}


def test_unknown_field_none_on_the_documented_shapes(tmp_path, capsys):
    result = _backfill(tmp_path, FakeAPI(api_key="right").transport)
    assert result.ok and result.unknown_fields == ()
    assert _manifest(result)["unknown_fields"] == []
    assert _warnings(capsys.readouterr().err) == []


def test_unknown_field_does_not_change_what_is_published(tmp_path):
    plain, planted = tmp_path / "plain", tmp_path / "planted"
    assert _backfill(plain, FakeAPI(api_key="right").transport).ok
    fake = FakeAPI(api_key="right")
    assert _backfill(planted, _planting(fake, "user_usage_report", lambda row: row.update(surprise_field=PLANTED))).ok

    def lines(root: Path):
        return {p.name: sorted(p.read_text(encoding="utf-8").splitlines()) for p in sorted((root / "records").rglob("*.jsonl"))}

    assert lines(plain) == lines(planted) and lines(plain)


def test_unknown_field_exit_code_through_main_is_still_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(KEY_ENV, "right")
    fake = FakeAPI(api_key="right")
    code = main(["backfill", "--since", "2026-09-01", "--data-dir", str(tmp_path)],
                transport=_planting(fake, "usage_report", lambda row: row.update(surprise_field=PLANTED)), now=lambda: NOW)
    assert code == 0 and len(_warnings(capsys.readouterr().err)) == 1
