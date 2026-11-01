"""F-41: a page loop ends. A server that keeps saying "more" without moving on
(the same page again, or a page other than the one asked for), or more pages
than the cap, stops the query with PaginationError (exit 2) instead of
requesting for hours.
"""

import json

import httpx
import pytest

from litellm_helpers import backfill, basic_rows, users
from meterline_litellm import fetch as fetch_module
from meterline_litellm.client import LitellmClient, PaginationError
from meterline_litellm.fetch import PATH_USER, _all_pages


def _body(page=1, has_more=True, marker="x"):
    return {"results": [], "metadata": {"has_more": has_more, "page": page, "total_pages": 99, "marker": marker}}


def _client(handler, clock):
    return LitellmClient("sk-test", base_url="https://proxy.example.com", transport=httpx.MockTransport(handler),
                         clock=clock, sleep=clock.sleep, jitter=lambda: 0, rate_per_minute=100000)


def test_cursor_repeat_a_server_that_ignores_page_returns_the_same_page(fake_clock):
    calls = []

    def handler(request):
        calls.append(request.url.params.get("page"))
        if len(calls) > 50:
            raise AssertionError("the loop did not stop")
        return httpx.Response(200, json=_body(page=1))

    with pytest.raises(PaginationError, match="page 2"):
        _all_pages(_client(handler, fake_clock), PATH_USER, {}, 1000)
    assert calls == ["1", "2"]


def test_cursor_repeat_a_page_other_than_the_one_asked_for_stops(fake_clock):
    def handler(request):
        asked = int(request.url.params["page"])
        return httpx.Response(200, json=_body(page=asked + 1 if asked == 2 else asked, marker="m%d" % asked))

    with pytest.raises(PaginationError, match="asked for page 2"):
        _all_pages(_client(handler, fake_clock), PATH_USER, {}, 1000)


def test_cursor_repeat_endless_distinct_pages_hit_the_page_cap(fake_clock, monkeypatch):
    monkeypatch.setattr(fetch_module, "MAX_PAGES", 5)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) > 50:
            raise AssertionError("the loop did not stop")
        return httpx.Response(200, json=_body(page=int(request.url.params["page"]), marker="m%d" % len(calls)))

    with pytest.raises(PaginationError, match="more than 5 pages"):
        _all_pages(_client(handler, fake_clock), PATH_USER, {}, 1000)
    assert len(calls) == 5


def test_cursor_repeat_a_normal_query_is_untouched(fake_clock):
    def handler(request):
        n = int(request.url.params["page"])
        return httpx.Response(200, json=_body(page=n, has_more=n < 3, marker="m%d" % n))

    assert len(_all_pages(_client(handler, fake_clock), PATH_USER, {}, 1000)) == 3


def test_cursor_repeat_ends_a_command_with_exit_2(make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a"))
    inner = proxy.transport
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) > 200:
            raise AssertionError("the loop did not stop")
        response = inner.handle_request(request)
        body = json.loads(response.read().decode("utf-8"))
        body["metadata"]["has_more"] = True
        body["metadata"]["page"] = 1
        return httpx.Response(200, json=body)

    proxy.transport = httpx.MockTransport(handler)
    rc = backfill(data_dir, proxy, fake_clock)
    err = capsys.readouterr().err
    assert rc == 2
    assert "page 2" in err and "Traceback" not in err
    assert len(calls) < 10
    assert not list(data_dir.rglob("*.jsonl"))
