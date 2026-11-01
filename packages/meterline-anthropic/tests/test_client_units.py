"""Extra unit tests for the client (no done-check names here)."""

import json

import httpx
import pytest

from anthropic_dataset import load_dataset
from meterline_anthropic.testing import FakeApi
from meterline_anthropic.client import (
    ActivityDateUnavailable,
    AnalyticsClient,
    ApiError,
)

ENDPOINT = "user_usage_report"
PARAMS = {
    "starting_at": "2026-07-01T00:00:00Z",
    "ending_at": "2026-08-01T00:00:00Z",
    "bucket_width": "1d",
    "limit": "1000",
}


def make(api, clock, key, **kw):
    kw.setdefault("jitter", lambda: 0.9999)  # top of the jitter range: full delay
    return AnalyticsClient(key, transport=api.transport, clock=clock, sleep=clock.sleep, **kw)


def test_backoff_doubles_and_is_capped(fake_api, fake_clock, fake_key_env):
    fake_api.fail_page(ENDPOINT, 1, 503, times=20)
    client = make(fake_api, fake_clock, fake_key_env, max_tries=9)
    with pytest.raises(ApiError) as info:
        client.get(ENDPOINT, PARAMS)
    assert info.value.status == 503
    assert fake_clock.sleeps == [1, 2, 4, 8, 16, 32, 60, 60]
    assert len(fake_api.requests_for(ENDPOINT)) == 9


def test_server_error_retried_then_succeeds(fake_api, fake_clock, fake_key_env):
    fake_api.fail_page(ENDPOINT, 1, 500, times=2)
    client = make(fake_api, fake_clock, fake_key_env)
    body = client.get(ENDPOINT, PARAMS)
    assert body["data"]
    assert [e.status for e in fake_api.requests_for(ENDPOINT)] == [500, 500, 200]
    assert fake_clock.sleeps == [1, 2]


def test_retry_after_larger_than_backoff_wins(fake_api, fake_clock, fake_key_env):
    fake_api.fail_page(ENDPOINT, 1, 429, retry_after=30)
    client = make(fake_api, fake_clock, fake_key_env)
    client.get(ENDPOINT, PARAMS)
    assert fake_clock.sleeps == [30]


def test_non_retryable_4xx_raises_without_key(fake_clock):
    api = FakeApi(load_dataset(), clock=fake_clock, api_key="the-right-key-value")
    wrong = "a-wrong-key-value-123"
    client = AnalyticsClient(wrong, transport=api.transport, clock=fake_clock, sleep=fake_clock.sleep)
    with pytest.raises(ApiError) as info:
        client.get(ENDPOINT, PARAMS)
    err = info.value
    assert err.status == 401
    assert wrong not in str(err) and wrong not in repr(err)
    assert len(api.log) == 1  # not retried
    assert fake_clock.sleeps == []


def test_key_echoed_by_server_is_masked(fake_clock):
    key = "echoed-key-value-456"

    def handler(request):
        msg = "bad key " + request.headers["x-api-key"]
        return httpx.Response(403, json={"error": {"type": "permission_error", "message": msg}})

    client = AnalyticsClient(key, transport=httpx.MockTransport(handler), clock=fake_clock, sleep=fake_clock.sleep)
    with pytest.raises(ApiError) as info:
        client.get(ENDPOINT, PARAMS)
    assert key not in str(info.value)
    assert "***" in str(info.value)


def test_exhausted_retries_message_has_no_key(fake_api, fake_clock, fake_key_env):
    fake_api.fail_page(ENDPOINT, 1, 502, times=10)
    client = make(fake_api, fake_clock, fake_key_env, max_tries=3)
    with pytest.raises(ApiError) as info:
        client.get(ENDPOINT, PARAMS)
    assert fake_key_env not in str(info.value)


def test_400_naming_a_date_raises_activity_date_unavailable(fake_api, fake_clock, fake_key_env):
    from datetime import date

    fake_api.set_latest_activity_date(date(2026, 9, 1))
    client = make(fake_api, fake_clock, fake_key_env)
    with pytest.raises(ActivityDateUnavailable) as info:
        client.get("users", {"date": "2026-09-02", "limit": "1000"})
    assert info.value.latest_date == date(2026, 9, 1)
    assert info.value.status == 400


def test_pagination_keeps_other_params_unchanged(dataset, fake_clock, fake_key_env):
    api = FakeApi(dataset, page_limit=500, clock=fake_clock, api_key=fake_key_env)
    params = dict(PARAMS)
    params["group_by[]"] = ["model", "product"]
    client = make(api, fake_clock, fake_key_env)
    pages = list(client.pages(ENDPOINT, params))
    assert len(pages) >= 3
    log = api.requests_for(ENDPOINT)
    assert len(log) == len(pages)
    assert "page" not in log[0].params
    for entry in log[1:]:
        rest = {k: v for k, v in entry.params.items() if k != "page"}
        assert rest == log[0].params
        assert "page" in entry.params
    assert params == dict(PARAMS, **{"group_by[]": ["model", "product"]})  # caller's dict untouched


def test_restart_gives_up_after_three_restarts(dataset, fake_clock, fake_key_env):
    api = FakeApi(dataset, page_limit=500, clock=fake_clock, api_key=fake_key_env)
    api.expire_cursor_on_page(ENDPOINT, 2, times=10)
    client = make(api, fake_clock, fake_key_env)
    with pytest.raises(ApiError) as info:
        list(client.pages(ENDPOINT, PARAMS))
    assert info.value.status == 410
    assert [e.page for e in api.requests_for(ENDPOINT)] == [1, 2] * 4


def test_headers_sent_and_key_not_in_params(fake_clock, fake_key_env):
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b'{"data": [], "has_more": false, "cost": 1.50}')

    client = AnalyticsClient(fake_key_env, transport=httpx.MockTransport(handler), clock=fake_clock, sleep=fake_clock.sleep)
    body = client.get("summaries", {"starting_date": "2026-07-01"})
    assert seen["headers"]["x-api-key"] == fake_key_env
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["user-agent"].startswith("meterline-anthropic/")
    assert fake_key_env not in seen["url"]
    from decimal import Decimal

    assert body["cost"] == Decimal("1.50") and isinstance(body["cost"], Decimal)


def test_fetch_pages_stops_on_null_next_page_without_has_more(fake_clock, fake_key_env):
    bodies = {
        None: {"data": [1], "next_page": "cursor-2"},
        "cursor-2": {"data": [2], "next_page": "cursor-3"},
        "cursor-3": {"data": [3], "next_page": None},
    }
    seen = []

    def handler(request):
        cursor = request.url.params.get("page")
        seen.append(cursor)
        return httpx.Response(200, json=bodies[cursor])

    client = AnalyticsClient(fake_key_env, transport=httpx.MockTransport(handler), clock=fake_clock, sleep=fake_clock.sleep)
    pages = client.fetch_pages("users", {"date": "2026-09-01"})
    assert [p.body["data"] for p in pages] == [[1], [2], [3]]
    assert seen == [None, "cursor-2", "cursor-3"]


def test_fetch_pages_stops_when_has_more_is_false(fake_clock, fake_key_env):
    def handler(request):
        return httpx.Response(200, json={"data": [], "has_more": False, "next_page": "ignored"})

    client = AnalyticsClient(fake_key_env, transport=httpx.MockTransport(handler), clock=fake_clock, sleep=fake_clock.sleep)
    assert len(client.fetch_pages("usage_report", {})) == 1
