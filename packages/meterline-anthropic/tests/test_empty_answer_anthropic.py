"""MF-3: an answer without its envelope is an error, and an empty answer is not proof that a day is loaded.

Before, a 200 without ``data`` (or with ``data: null``) gave exit 0 with no records and put the days in the loaded list,
so ``daily`` never asked for them again; a missing ``actor.email_address`` gave a null e-mail without a word.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from meterline_anthropic.cli import main

NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)
ENDPOINTS = ("user_usage_report", "user_cost_report", "usage_report", "cost_report", "summaries", "users")


def _answering(fake_api, endpoint, edit):
    """A transport that applies ``edit(body)`` to every 200 answer of ``endpoint``."""

    def handler(request: httpx.Request) -> httpx.Response:
        response = fake_api.transport.handle_request(request)
        if response.status_code != 200 or not request.url.path.endswith("/" + endpoint):
            return response
        body = json.loads(response.read().decode("utf-8"))
        edit(body)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _backfill(transport, clock, data_dir, since="2026-10-01", now=NOW):
    return main(
        ["backfill", "--since", since, "--data-dir", str(data_dir)],
        transport=transport, clock=clock, sleep=clock.sleep, now=lambda: now, jitter=lambda: 0,
    )


def _state(data_dir):
    path = Path(data_dir) / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _no_records(data_dir):
    return not list(Path(data_dir).glob("records/*/*.jsonl"))


def _manifests(data_dir):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((Path(data_dir) / "runs").glob("*.json"))]


@pytest.mark.parametrize("how", ["missing", "null"])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_missing_envelope_anthropic(endpoint, how, fake_api, fake_clock, tmp_path, capsys):
    def edit(body):
        body.pop("data", None)
        if how == "null":
            body["data"] = None

    rc = _backfill(_answering(fake_api, endpoint, edit), fake_clock, tmp_path)
    err = capsys.readouterr().err
    assert rc == 2, err
    assert endpoint in err and "data" in err
    assert _no_records(tmp_path)
    state = _state(tmp_path)
    assert not state.get("activity_loaded") and not state.get("summaries_loaded")
    assert [m["status"] for m in _manifests(tmp_path)] == ["failed"]


def test_missing_envelope_anthropic_result_list_of_a_bucket(fake_api, fake_clock, tmp_path, capsys):
    def edit(body):
        for bucket in body["data"]:
            bucket.pop("results", None)

    assert _backfill(_answering(fake_api, "usage_report", edit), fake_clock, tmp_path) == 2
    assert "results" in capsys.readouterr().err
    assert _no_records(tmp_path)


@pytest.mark.parametrize("endpoint", ["user_usage_report", "user_cost_report"])
def test_missing_actor_email_address_anthropic(endpoint, fake_api, fake_clock, tmp_path, capsys):
    def edit(body):
        for row in body["data"]:
            row["actor"].pop("email_address", None)

    rc = _backfill(_answering(fake_api, endpoint, edit), fake_clock, tmp_path)
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "email_address" in err
    assert _no_records(tmp_path)


def test_empty_answer_anthropic_inside_the_revision_period_stays_unloaded(fake_api, fake_clock, tmp_path, capsys):
    """Every day of the month asked for is empty and the month is still open: nothing counts as loaded."""

    def blank(body):
        body["data"] = []

    transport = _answering(fake_api, "users", blank)
    transport = _answering_chain(transport, "summaries", blank)
    assert _backfill(transport, fake_clock, tmp_path) == 0, capsys.readouterr().err
    state = _state(tmp_path)
    assert state["activity_loaded"] == [] and state["summaries_loaded"] == []
    assert _manifests(tmp_path)[0]["empty_days"] == 0


def test_empty_answer_anthropic_after_the_revision_period_is_loaded_and_counted(fake_api, fake_clock, tmp_path, capsys):
    """May and June 2026 are empty in the fake and long final: they are loaded, and the manifest counts them."""
    rc = _backfill(fake_api.transport, fake_clock, tmp_path, since="2026-06-25")
    assert rc == 0, capsys.readouterr().err
    state = _state(tmp_path)
    for day in ("2026-06-25", "2026-06-30"):
        assert day in state["activity_loaded"] and day in state["summaries_loaded"]
    # 6 empty days (June 25-30) in each of the two series; July 1 on has data
    assert _manifests(tmp_path)[0]["empty_days"] == 12


def test_empty_answer_anthropic_open_day_is_asked_again_by_daily(fake_api, fake_clock, tmp_path, capsys):
    """2026-10-02 comes back empty on the first run: it and the days after it stay unloaded, and the next daily asks again."""

    def handler(request: httpx.Request) -> httpx.Response:
        response = fake_api.transport.handle_request(request)
        if response.status_code == 200 and request.url.path.endswith("/users") and request.url.params.get("date") == "2026-10-02":
            return httpx.Response(200, json={"data": [], "next_page": None})
        return response

    assert _backfill(httpx.MockTransport(handler), fake_clock, tmp_path) == 0, capsys.readouterr().err
    assert _state(tmp_path)["activity_loaded"] == ["2026-10-01"]
    later = datetime(2026, 10, 5, 18, 0, 0, tzinfo=timezone.utc)
    rc = main(["daily", "--data-dir", str(tmp_path)], transport=fake_api.transport, clock=fake_clock, sleep=fake_clock.sleep,
              now=lambda: later, jitter=lambda: 0)
    assert rc == 0, capsys.readouterr().err
    assert _state(tmp_path)["activity_loaded"] == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]


def _answering_chain(transport, endpoint, edit):
    def handler(request: httpx.Request) -> httpx.Response:
        response = transport.handle_request(request)
        if response.status_code != 200 or not request.url.path.endswith("/" + endpoint):
            return response
        body = json.loads(response.read().decode("utf-8"))
        edit(body)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)
