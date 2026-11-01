"""F-41: a page loop ends. A cursor that repeats, or more pages than the cap,
stops the query with PaginationError (exit 2 at the command line) instead of
requesting forever.
"""

import json
from datetime import datetime, timezone

import httpx
import pytest

from meterline_anthropic import client as client_module
from meterline_anthropic.cli import main
from meterline_anthropic.client import AnalyticsClient, PaginationError

NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)


def _page(next_page, has_more=True):
    return {"data": [], "has_more": has_more, "next_page": next_page, "organization_id": "org-x",
            "data_refreshed_at": "2026-10-04T12:00:00Z"}


def _client(handler, clock, **kw):
    return AnalyticsClient("k-test", transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep,
                           jitter=lambda: 0, rate_per_minute=100000, **kw)


def test_cursor_repeat_the_same_token_forever_stops_after_two_requests(fake_clock):
    calls = []

    def handler(request):
        calls.append(request.url.params.get("page"))
        if len(calls) > 50:
            raise AssertionError("the loop did not stop")
        return httpx.Response(200, json=_page("same"))

    with _client(handler, fake_clock) as client:
        with pytest.raises(PaginationError, match="cursor repeats"):
            client.fetch_pages("user_usage_report", {"limit": "1"})
    assert calls == [None, "same"]


def test_cursor_repeat_a_cycle_of_tokens_stops(fake_clock):
    order = ["a", "b", "a", "b"]
    calls = []

    def handler(request):
        calls.append(request.url.params.get("page"))
        return httpx.Response(200, json=_page(order[len(calls) - 1] if len(calls) <= len(order) else "a"))

    with _client(handler, fake_clock) as client:
        with pytest.raises(PaginationError, match="cursor repeats"):
            client.fetch_pages("usage_report", {})
    assert len(calls) <= 4


def test_cursor_repeat_endless_new_cursors_hit_the_page_cap(fake_clock, monkeypatch):
    monkeypatch.setattr(client_module, "MAX_PAGES", 5)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) > 50:
            raise AssertionError("the loop did not stop")
        return httpx.Response(200, json=_page("token-%d" % len(calls)))

    with _client(handler, fake_clock) as client:
        with pytest.raises(PaginationError, match="more than 5 pages"):
            client.fetch_pages("user_cost_report", {})
    assert len(calls) == 5


def test_cursor_repeat_the_cap_can_be_set_on_the_client(fake_clock):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=_page("token-%d" % len(calls)) if len(calls) < 50 else _page(None, False))

    with _client(handler, fake_clock, max_pages=3) as client:
        with pytest.raises(PaginationError, match="more than 3 pages"):
            client.fetch_pages("cost_report", {})
    assert len(calls) == 3


def test_cursor_repeat_a_normal_query_is_untouched(fake_clock):
    bodies = [_page("t1"), _page("t2"), _page(None, False)]
    calls = []

    def handler(request):
        calls.append(request.url.params.get("page"))
        return httpx.Response(200, json=bodies[len(calls) - 1])

    with _client(handler, fake_clock) as client:
        pages = client.fetch_pages("usage_report", {})
    assert len(pages) == 3 and calls == [None, "t1", "t2"]


def test_cursor_repeat_ends_a_command_with_exit_2(fake_clock, tmp_path, capsys, fake_key_env):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) > 200:
            raise AssertionError("the loop did not stop")
        return httpx.Response(200, json=_page("same"))

    rc = main(
        ["backfill", "--since", "2026-10-01", "--data-dir", str(tmp_path)],
        transport=httpx.MockTransport(handler), clock=fake_clock, sleep=fake_clock.sleep, now=lambda: NOW,
        jitter=lambda: 0,
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "cursor repeats" in err and "Traceback" not in err
    failed = [json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*.json")]
    assert failed and failed[0]["status"] == "failed"
    assert len(calls) < 10
