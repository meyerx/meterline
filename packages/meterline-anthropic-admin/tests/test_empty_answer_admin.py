"""MF-3: an answer without its envelope is an error, and an empty answer is not proof that a day is loaded.

The Admin connector already refused a missing ``data`` or ``results`` (those cases pass at the commit before this one);
what it did was record every day of an empty answer as loaded, so ``daily`` never asked for the days that were still
open again.
"""

import json
from pathlib import Path

import httpx
import pytest

from admin_helpers import backfill, basic_cost, basic_usage, manifests, no_records


def _edited(api, edit):
    def handler(request):
        response = api.transport.handle_request(request)
        if response.status_code != 200:
            return response
        body = json.loads(response.read().decode("utf-8"))
        edit(body)
        return httpx.Response(200, json=body)

    class Wrapped:
        transport = httpx.MockTransport(handler)

    return Wrapped


def _state(data_dir):
    return json.loads((Path(data_dir) / "state.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("how", ["missing", "null"])
def test_missing_envelope_admin_data(make_api, fake_clock, data_dir, capsys, how):
    def edit(body):
        body.pop("data", None)
        if how == "null":
            body["data"] = None

    assert backfill(data_dir, _edited(make_api(basic_usage(), basic_cost()), edit), fake_clock) == 2
    assert "data" in capsys.readouterr().err
    assert no_records(data_dir)
    assert not (Path(data_dir) / "state.json").exists() or _state(data_dir)["days_loaded"] == []


@pytest.mark.parametrize("how", ["missing", "null"])
def test_missing_envelope_admin_results(make_api, fake_clock, data_dir, capsys, how):
    def edit(body):
        for bucket in body["data"]:
            bucket.pop("results", None)
            if how == "null":
                bucket["results"] = None

    assert backfill(data_dir, _edited(make_api(basic_usage(), basic_cost()), edit), fake_clock) == 2
    assert "results" in capsys.readouterr().err
    assert no_records(data_dir)


def test_empty_answer_admin_loads_only_the_days_that_can_no_longer_change(make_api, fake_clock, data_dir, capsys):
    """Everything is empty. Of 2026-09-14..19 pulled on 09-20 06:00 UTC, only 14..17 are final (48 hours after they ended)."""
    api = make_api([], [])
    assert backfill(data_dir, api, fake_clock) == 0, capsys.readouterr().err
    assert _state(data_dir)["days_loaded"] == ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"]
    assert manifests(data_dir)[0]["empty_days"] == 4
