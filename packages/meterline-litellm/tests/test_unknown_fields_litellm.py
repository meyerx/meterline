"""A59: a field the provider sent and the connector does not know is reported, not fatal.

The run publishes as usual and ends with the status it would have had; the manifest lists the field under
``unknown_fields`` (endpoint, key path, count, never a value), one warning goes to stderr, and ``RunResult`` carries the
same list. A required metric that is missing or mistyped still fails the run (test_missing_required_fields_litellm.py).
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from meterline_litellm import run
from meterline_litellm.cli import main
from meterline_litellm.testing import FakeAPI

PLANTED = "value-that-must-never-be-printed"
NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
CREDS = {"LITELLM_API_KEY": "sk-synthetic-not-a-real-key", "LITELLM_BASE_URL": "https://proxy.example.com"}


def _hooks(clock):
    return {"clock": clock, "sleep": clock.sleep, "jitter": lambda: 0}


def _planting(fake, path_end, edit):
    """A transport that applies ``edit(body)`` to every 200 answer of the route ending in ``path_end``."""

    def handler(request: httpx.Request) -> httpx.Response:
        response = fake.transport.handle_request(request)
        if response.status_code != 200 or not request.url.path.endswith(path_end):
            return response
        body = json.loads(response.read().decode("utf-8"))
        edit(body)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _backfill(tmp_path, clock, transport, **kw):
    return run("backfill", since=date(2026, 9, 1), proxy_name="my-proxy", data_dir=tmp_path, now=lambda: NOW,
               credentials=CREDS, transport=transport, **_hooks(clock), **kw)


def _manifest(result):
    return json.loads(result.manifest_path.read_text(encoding="utf-8"))


def _warnings(err):
    return [line for line in err.splitlines() if line.startswith("warning:")]


def _plant_everywhere(body):
    body["new_page_key"] = PLANTED
    for result in body["results"]:
        result["new_result_key"] = PLANTED
        result["metrics"]["new_metric"] = PLANTED
        for item in result["breakdown"]["models"].values():
            item["metrics"]["new_metric"] = PLANTED
        result["breakdown"]["new_kind"] = {"x": PLANTED}


def test_unknown_field_is_listed_in_the_manifest_and_the_run_still_publishes(fake_clock, tmp_path, capsys):
    fake = FakeAPI(clock=fake_clock)
    result = _backfill(tmp_path, fake_clock, _planting(fake, "/user/daily/activity", _plant_everywhere))
    err = capsys.readouterr().err
    assert result.exit_code == 0 and result.ok and sum(result.records.values()) > 0
    manifest = _manifest(result)
    assert manifest["status"] == "published"
    by_path = {u["path"]: u for u in manifest["unknown_fields"]}
    assert set(by_path) == {"new_page_key", "results[].new_result_key", "results[].metrics.new_metric",
                            "results[].breakdown.new_kind", "results[].breakdown.models.*.metrics.new_metric"}
    assert {u["endpoint"] for u in manifest["unknown_fields"]} == {"user_daily_activity"}
    assert by_path["new_page_key"]["count"] >= 1 and by_path["results[].new_result_key"]["count"] >= 1
    assert result.unknown_fields == tuple(manifest["unknown_fields"])
    assert len(_warnings(err)) == 1 and "user_daily_activity" in _warnings(err)[0]
    assert PLANTED not in err and PLANTED not in json.dumps(manifest)


def test_unknown_field_in_the_user_list_envelope_is_listed(fake_clock, tmp_path):
    fake = FakeAPI(clock=fake_clock)
    result = _backfill(tmp_path, fake_clock, _planting(fake, "/user/list", lambda body: body.update(new_envelope_key=PLANTED)))
    assert result.ok
    assert [(u["endpoint"], u["path"]) for u in result.unknown_fields] == [("user_list", "new_envelope_key")]


def test_unknown_field_none_on_the_documented_shapes(fake_clock, tmp_path, capsys):
    result = _backfill(tmp_path, fake_clock, FakeAPI(clock=fake_clock).transport)
    assert result.ok and result.unknown_fields == ()
    assert _manifest(result)["unknown_fields"] == []
    assert _warnings(capsys.readouterr().err) == []


def test_unknown_field_does_not_change_what_is_published(fake_clock, tmp_path):
    plain, planted = tmp_path / "plain", tmp_path / "planted"
    assert _backfill(plain, fake_clock, FakeAPI(clock=fake_clock).transport).ok
    fake = FakeAPI(clock=fake_clock)
    assert _backfill(planted, fake_clock, _planting(fake, "/user/daily/activity", _plant_everywhere)).ok

    def lines(root: Path):
        return {p.name: sorted(p.read_text(encoding="utf-8").splitlines()) for p in sorted((root / "records").rglob("*.jsonl"))}

    assert lines(plain) == lines(planted) and lines(plain)


def test_unknown_field_exit_code_through_main_is_still_zero(fake_clock, tmp_path, capsys):
    fake = FakeAPI(clock=fake_clock)
    code = main(["backfill", "--proxy-name", "my-proxy", "--since", "2026-09-01", "--data-dir", str(tmp_path)],
                transport=_planting(fake, "/user/daily/activity", _plant_everywhere), now=lambda: NOW, **_hooks(fake_clock))
    assert code == 0 and len(_warnings(capsys.readouterr().err)) == 1
