"""A01 to A03 (spec section 8): the client's headers and array parameters, pagination, and the 429 wait."""

import httpx
import pytest

from admin_helpers import NOW, backfill, basic_cost, basic_usage
from admin_helpers import BASE_URL, FAKE_KEY
from meterline_anthropic_admin.testing import COST_PATH, USAGE_PATH
from meterline_anthropic_admin import __version__
from meterline_anthropic_admin.client import AdminClient, ApiError, PaginationError

GROUP_BY = ["api_key_id", "workspace_id", "model"]
WINDOW = {"starting_at": "2026-09-15T00:00:00Z", "ending_at": "2026-09-20T00:00:00Z", "bucket_width": "1d", "limit": 2}


def _client(api, clock, **kw):
    return AdminClient(FAKE_KEY, base_url=BASE_URL, transport=api.transport, clock=clock, sleep=clock.sleep, jitter=lambda: 0, **kw)


def test_done_check_A01_sends_key_version_and_arrays_as_group_by_brackets():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [], "has_more": False, "next_page": None})

    clock = httpx.MockTransport(handler)
    with AdminClient(FAKE_KEY, base_url=BASE_URL, transport=clock) as client:
        client.get_page(USAGE_PATH, {"starting_at": "2026-09-15T00:00:00Z", "group_by[]": GROUP_BY})
    request = seen[0]
    assert request.headers["x-api-key"] == FAKE_KEY
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.headers["user-agent"] == "meterline-anthropic-admin/%s" % __version__
    assert request.url.host == "api.anthropic.com" and request.url.path == USAGE_PATH
    assert request.url.params.get_list("group_by[]") == GROUP_BY
    assert "group_by%5B%5D=api_key_id&group_by%5B%5D=workspace_id&group_by%5B%5D=model" in str(request.url)


def test_done_check_A01_a_backfill_asks_for_arrays_in_both_reports(make_api, fake_clock, data_dir):
    api = make_api(basic_usage(), basic_cost())
    assert backfill(data_dir, api, fake_clock) == 0
    usage = api.requests_for(USAGE_PATH)[0]
    cost = api.requests_for(COST_PATH)[0]
    assert set(GROUP_BY) <= set(usage.params["group_by[]"])
    assert cost.params["group_by[]"] == ["description", "workspace_id"]
    for entry in (usage, cost):
        assert entry.headers["x-api-key"] == FAKE_KEY and entry.headers["anthropic-version"] == "2023-06-01"
        assert entry.params["bucket_width"] == ["1d"] and entry.params["limit"] == ["31"]


def test_done_check_A02_follows_next_page_until_has_more_is_false(make_api, fake_clock):
    api = make_api(basic_usage())
    with _client(api, fake_clock) as client:
        pages = client.fetch_pages(USAGE_PATH, {**WINDOW, "group_by[]": GROUP_BY})
    assert len(pages) == 3  # five days, two buckets a page
    assert [e.params.get("page") for e in api.requests_for(USAGE_PATH)] == [None, ["page_2"], ["page_4"]]
    for entry in api.requests_for(USAGE_PATH):  # every other parameter is repeated unchanged
        assert entry.params["starting_at"] == [WINDOW["starting_at"]] and entry.params["group_by[]"] == GROUP_BY
    days = [b["starting_at"][:10] for page in pages for b in page.body["data"]]
    assert days == ["2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19"]
    assert pages[-1].body["has_more"] is False


def test_done_check_A02_a_repeating_cursor_is_an_error(make_api, fake_clock):
    api = make_api(basic_usage(), repeat_cursor=True)
    with _client(api, fake_clock) as client, pytest.raises(PaginationError, match="cursor repeats"):
        client.fetch_pages(USAGE_PATH, dict(WINDOW, limit=1))


def test_done_check_A02_more_pages_than_the_cap_is_an_error(make_api, fake_clock):
    api = make_api(basic_usage())
    with _client(api, fake_clock, max_pages=2) as client, pytest.raises(PaginationError, match="more than 2 pages"):
        client.fetch_pages(USAGE_PATH, dict(WINDOW, limit=1))


def test_done_check_A02_cli_pages_through_a_cut_window_and_fails_on_a_loop(make_api, fake_clock, data_dir, tmp_path):
    api = make_api(basic_usage(), basic_cost(), force_limit=2)
    assert backfill(data_dir, api, fake_clock, "2026-09-15") == 0
    assert len(api.requests_for(USAGE_PATH)) == 3  # 15th to 19th, two buckets a page
    looping = make_api(basic_usage(), basic_cost(), force_limit=1, repeat_cursor=True)
    other = tmp_path / "loop"
    assert backfill(other, looping, fake_clock, "2026-09-15") == 2
    assert not (other / "records").exists()


def _first_429(api, retry_after, clock, **kw):
    api.fail(USAGE_PATH, 429, times=1, retry_after=retry_after)
    with _client(api, clock, **kw) as client:
        client.get_page(USAGE_PATH, {"starting_at": "2026-09-15T00:00:00Z", "ending_at": "2026-09-16T00:00:00Z"})
    return clock.sleeps


def test_done_check_A03_429_waits_for_retry_after(make_api, fake_clock):
    assert _first_429(make_api(basic_usage()), 120, fake_clock, wall=fake_clock) == [120]


def test_done_check_A03_the_wait_is_capped_at_300_seconds(make_api, fake_clock):
    assert _first_429(make_api(basic_usage()), 99999999, fake_clock, wall=fake_clock) == [300]


def test_done_check_A03_the_cap_is_configurable(make_api, fake_clock):
    assert _first_429(make_api(basic_usage()), 99999999, fake_clock, wall=fake_clock, max_retry_after=9) == [9]


@pytest.mark.parametrize("value", ["Infinity", "NaN", "garbage", "-5", "1e999999999"])
def test_done_check_A03_a_retry_after_that_is_not_usable_falls_back_to_backoff(make_api, fake_clock, value):
    assert 1 <= _first_429(make_api(basic_usage()), value, fake_clock, wall=fake_clock)[0] <= 300


def test_done_check_A03_cli_option_and_exhausted_retries(make_api, fake_clock, data_dir, tmp_path):
    api = make_api(basic_usage(), basic_cost())
    api.fail(USAGE_PATH, 429, times=1, retry_after=99999999)
    assert backfill(data_dir, api, fake_clock, "2026-09-14", NOW, "--max-retry-after", "9") == 0
    assert fake_clock.sleeps[0] == 9
    down = make_api(basic_usage(), basic_cost())
    down.fail(USAGE_PATH, 500, times=99)
    assert backfill(tmp_path / "down", down, fake_clock) == 1  # an API failure: exit 1, state kept
    with _client(down, fake_clock) as client, pytest.raises(ApiError) as caught:
        client.get_page(USAGE_PATH, {"starting_at": "2026-09-15T00:00:00Z"})
    assert caught.value.status == 500


def test_done_check_A03_the_client_throttles_to_its_rate(make_api, fake_clock):
    api = make_api(basic_usage())
    with _client(api, fake_clock, rate_per_minute=2) as client:
        for _ in range(3):
            client.get_page(USAGE_PATH, {"starting_at": "2026-09-15T00:00:00Z", "ending_at": "2026-09-16T00:00:00Z"})
    assert fake_clock.sleeps and fake_clock.sleeps[0] > 0  # the third call inside one minute waited
    assert client.request_count == 3
