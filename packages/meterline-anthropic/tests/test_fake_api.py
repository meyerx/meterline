"""Checks that the fake API itself behaves as the plan describes."""

import json
from datetime import date
from decimal import Decimal

import httpx

BASE = "https://api.anthropic.com/v1/organizations/analytics/"
HEADERS = {"anthropic-version": "2023-06-01"}


def make_client(api, key="test-key-not-real"):
    headers = dict(HEADERS)
    headers["x-api-key"] = key
    return httpx.Client(transport=api.transport, base_url=BASE, headers=headers)


def usage_params(**over):
    p = {
        "starting_at": "2026-07-01T00:00:00Z",
        "ending_at": "2026-08-01T00:00:00Z",
        "bucket_width": "1d",
        "group_by[]": ["model", "product"],
        "limit": "1000",
    }
    p.update(over)
    return p


def test_dataset_shape(dataset):
    assert dataset["meta"]["synthetic"] is True
    assert len(dataset["users"]) == 50
    assert len({u["user_id"] for u in dataset["users"]}) == 50
    assert all(u["email"].endswith("@example.com") for u in dataset["users"])
    assert min(dataset["usage"]) == "2026-07-01" and max(dataset["usage"]) == "2026-10-08"
    pinned = dataset["meta"]["pinned_cost_row"]
    rows = [
        r for r in dataset["cost"][pinned["date"]]
        if r["actor"]["user_id"] == pinned["actor_id"]
        and r["product"] == pinned["product"] and r["model"] == pinned["model"]
    ]
    assert [r["amount"] for r in rows] == ["41280.000000"]


def test_org_totals_equal_user_sums_plus_api_bucket(dataset):
    for dkey in ("2026-07-15", "2026-09-15"):
        user_cost = sum(Decimal(r["amount"]) for r in dataset["cost"][dkey])
        [org_row] = dataset["org_cost"][dkey]  # no group_by: one row per day
        api_part = Decimal(org_row["amount"]) - user_cost
        assert api_part > 0


def test_pagination_counts_rows_and_follows_cursor(fake_api):
    client = make_client(fake_api)
    params = usage_params(limit="1000")
    seen = []
    page = 0
    while True:
        resp = client.get("user_usage_report", params=params)
        assert resp.status_code == 200
        body = resp.json()
        page += 1
        assert "organization_id" in body and "data_refreshed_at" in body
        assert all("starting_at" in row and "results" not in row for row in body["data"])
        count = len(body["data"])
        seen.append(count)
        if not body["has_more"]:
            assert body["next_page"] is None
            break
        assert count == 1000
        params = dict(params, page=body["next_page"])
    assert page >= 2
    expected = sum(len(v) for k, v in fake_api.dataset["usage"].items() if k < "2026-08-01")
    assert sum(seen) == expected
    assert [e.page for e in fake_api.requests_for("user_usage_report")] == list(range(1, page + 1))


def test_page_limit_caps_server_side(dataset, fake_clock):
    from meterline_anthropic.testing import FakeApi

    api = FakeApi(dataset, page_limit=200, clock=fake_clock)
    body = make_client(api, key="anything").get("user_usage_report", params=usage_params()).json()
    assert len(body["data"]) == 200


def test_cursor_is_bound_to_other_params(fake_api):
    client = make_client(fake_api)
    first = client.get("user_usage_report", params=usage_params()).json()
    cursor = first["next_page"]
    assert cursor
    changed = usage_params(ending_at="2026-08-02T00:00:00Z", page=cursor)
    resp = client.get("user_usage_report", params=changed)
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "invalid_request_error"
    ok = client.get("user_usage_report", params=usage_params(page=cursor))
    assert ok.status_code == 200


def test_scripted_429_carries_retry_after_then_recovers(fake_api, fake_clock):
    fake_api.fail_page("user_usage_report", 1, 429, retry_after=17, times=2)
    client = make_client(fake_api)
    for _ in range(2):
        resp = client.get("user_usage_report", params=usage_params())
        assert resp.status_code == 429
        assert resp.headers["retry-after"] == "17"
    fake_clock.advance(17)
    assert client.get("user_usage_report", params=usage_params()).status_code == 200
    log = fake_api.requests_for("user_usage_report")
    assert [e.status for e in log] == [429, 429, 200]
    assert log[2].time == log[0].time + 17


def test_cursor_expiry_gives_410_once(fake_api):
    fake_api.expire_cursor_on_page("user_usage_report", 2)
    client = make_client(fake_api)
    cursor = client.get("user_usage_report", params=usage_params()).json()["next_page"]
    gone = client.get("user_usage_report", params=usage_params(page=cursor))
    assert gone.status_code == 410
    again = client.get("user_usage_report", params=usage_params(page=cursor))
    assert again.status_code == 200


def test_activity_400_names_latest_date(fake_api):
    fake_api.set_latest_activity_date(date(2026, 10, 5))
    client = make_client(fake_api)
    ok = client.get("users", params={"date": "2026-10-05", "limit": "1000"})
    assert ok.status_code == 200 and ok.json()["data"]
    bad = client.get("users", params={"date": "2026-10-06", "limit": "1000"})
    assert bad.status_code == 400
    assert "latest available date is 2026-10-05" in bad.json()["error"]["message"]


def test_summaries_and_mutation(fake_api):
    client = make_client(fake_api)
    body = client.get(
        "summaries", params={"starting_date": "2026-09-01", "ending_date": "2026-09-04"}
    ).json()
    # ending_date is exclusive; the whole range is one response
    assert [r["starting_at"][:10] for r in body["data"]] == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert body["next_page"] is None
    assert "data_refreshed_at" not in body and "organization_id" not in body

    def bump(ds):
        ds["usage"]["2026-07-01"][0]["output_tokens"] += 1

    before = client.get("user_usage_report", params=usage_params()).json()
    first_before = before["data"][0]["output_tokens"]
    fake_api.mutate(bump)
    after = client.get("user_usage_report", params=usage_params()).json()
    assert after["data"][0]["output_tokens"] == first_before + 1


def test_request_log_uses_injected_clock(fake_api, fake_clock):
    client = make_client(fake_api)
    client.get("summaries", params={"starting_date": "2026-09-01", "ending_date": "2026-09-01"})
    fake_clock.advance(5)
    client.get("summaries", params={"starting_date": "2026-09-01", "ending_date": "2026-09-01"})
    times = [e.time for e in fake_api.log]
    assert times[1] - times[0] == 5


def test_wrong_key_is_rejected(fake_api):
    resp = make_client(fake_api, key="wrong").get("summaries", params={"starting_date": "a", "ending_date": "b"})
    assert resp.status_code == 401
    assert "wrong" not in json.dumps(resp.json())


def test_users_pages_by_next_page_only(dataset, fake_clock):
    from meterline_anthropic.testing import FakeApi

    api = FakeApi(dataset, page_limit=10, clock=fake_clock)
    client = make_client(api, key="anything")
    params = {"date": "2026-09-15", "limit": "1000"}
    got, pages = 0, 0
    while True:
        body = client.get("users", params=params).json()
        pages += 1
        assert set(body) == {"data", "next_page"}
        got += len(body["data"])
        if body["next_page"] is None:
            break
        params = dict(params, page=body["next_page"])
    assert pages > 1
    assert got == len(dataset["activity"]["2026-09-15"])


def test_refreshed_at_can_be_null_or_missing(dataset, fake_clock):
    from meterline_anthropic.testing import FakeApi

    api = FakeApi(dataset, clock=fake_clock)
    api.set_refreshed_at_for_window(date(2026, 7, 1), None)
    client = make_client(api, key="anything")
    body = client.get("usage_report", params=usage_params(limit="1")).json()
    assert "data_refreshed_at" in body and body["data_refreshed_at"] is None
    other = client.get("usage_report", params=usage_params(starting_at="2026-07-02T00:00:00Z")).json()
    assert other["data_refreshed_at"]
    api.omit_refreshed_at = True
    assert "data_refreshed_at" not in client.get("usage_report", params=usage_params()).json()


def test_org_report_buckets_and_no_group_by(fake_api):
    params = {k: v for k, v in usage_params(limit="31").items() if k != "group_by[]"}
    body = make_client(fake_api).get("usage_report", params=params).json()
    assert len(body["data"]) == 31 and body["has_more"] is False
    assert all(len(b["results"]) == 1 for b in body["data"])
