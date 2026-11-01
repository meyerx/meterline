"""MF-3: an answer without its envelope is an error, and an empty answer is not proof that a day is loaded.

Before, a 200 without ``results`` (or with ``results: null``) gave exit 0 with no records and recorded the days as loaded,
so ``daily`` never asked for them again; a missing ``metadata`` ended the paging without a word.
"""

import json
from pathlib import Path

import httpx
import pytest

from litellm_helpers import backfill, basic_rows, manifests, users
from meterline_litellm.testing import PATH_A


def _answering(proxy, edit):
    """Stands in for a ``FakeAPI``: ``edit(body)`` is applied to every 200 answer of the user activity endpoint."""

    def handler(request):
        response = proxy._handle(request)
        if response.status_code != 200 or not request.url.path.endswith(PATH_A):
            return response
        body = json.loads(response.text)
        edit(body)
        return httpx.Response(200, json=body)

    class Wrapped:
        transport = httpx.MockTransport(handler)

    return Wrapped


def _state(data_dir):
    path = Path(data_dir) / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"days_loaded": []}


def _no_records(data_dir):
    return not list(Path(data_dir).glob("records/*/*.jsonl"))


@pytest.mark.parametrize("key", ["results", "metadata"])
@pytest.mark.parametrize("how", ["missing", "null"])
def test_missing_envelope_litellm(key, how, make_proxy, fake_clock, data_dir, capsys):
    def edit(body):
        body.pop(key, None)
        if how == "null":
            body[key] = None

    proxy = _answering(make_proxy(basic_rows(), users=users("user-a", "user-b")), edit)
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 2
    err = capsys.readouterr().err
    assert key in err and "nothing published" in err
    assert _no_records(data_dir)
    assert _state(data_dir)["days_loaded"] == []


def test_empty_answer_litellm_loads_only_the_days_that_can_no_longer_change(make_proxy, fake_clock, data_dir, capsys):
    """Everything is empty. Of 2026-09-14..19 pulled on 09-20 06:00 UTC, only 14..17 are final (two full days after they ended)."""
    assert backfill(data_dir, make_proxy([], users=[]), fake_clock) == 0, capsys.readouterr().err
    assert _state(data_dir)["days_loaded"] == ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"]
    assert manifests(data_dir)[0]["empty_days"] == 4


def test_empty_answer_litellm_a_day_with_rows_is_loaded_while_open(make_proxy, fake_clock, data_dir, capsys):
    assert backfill(data_dir, make_proxy(basic_rows(), users=users("user-a", "user-b")), fake_clock) == 0, capsys.readouterr().err
    loaded = _state(data_dir)["days_loaded"]
    assert "2026-09-15" in loaded and "2026-09-16" in loaded and "2026-09-19" not in loaded
