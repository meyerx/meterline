"""Done-check 12 (spec v1.1 section 7): the fake API answers in the documented
response shapes of ``docs/api-shapes/`` (keys and nesting; values ignored)."""

from pathlib import Path

import httpx

from meterline.conformance import compare_shape
from meterline_anthropic.testing import FakeApi

SHAPES = Path(__file__).resolve().parents[3] / "docs" / "api-shapes"

# Fields the spec marks optional: the fake may leave them out.
OPTIONAL = {"chat_cowork_unified_metrics", "last_activity_date"}

DAY = {"starting_at": "2026-09-15T00:00:00Z", "ending_at": "2026-09-16T00:00:00Z", "bucket_width": "1d"}
REQUESTS = {
    "user_usage_report": {**DAY, "group_by[]": ["model", "product"], "limit": "1000"},
    "user_cost_report": {**DAY, "group_by[]": ["model", "product"], "limit": "1000"},
    "usage_report": {**DAY, "limit": "31"},
    "cost_report": {**DAY, "limit": "31"},
    "users": {"date": "2026-09-15", "limit": "1000"},
    "summaries": {"starting_date": "2026-09-01", "ending_date": "2026-09-30"},
}


def test_done_check_12_fake_answers_in_documented_shape(dataset, fake_clock, fake_key_env):
    api = FakeApi(dataset, clock=fake_clock, api_key=fake_key_env)
    problems = []
    with httpx.Client(transport=api.transport, base_url="https://fake.invalid/v1/organizations/analytics/") as http:
        for endpoint in sorted(REQUESTS):
            response = http.get(
                endpoint,
                params=REQUESTS[endpoint],
                headers={"x-api-key": fake_key_env, "anthropic-version": "2023-06-01"},
            )
            if response.status_code != 200:
                problems.append(f"{endpoint}: HTTP {response.status_code}")
                continue
            body = response.json()
            if not body.get("data"):
                problems.append(f"{endpoint}: no rows, nothing compared")
                continue
            # the first five differences are enough to show what drifted; keeps the report short
            problems.extend(
                f"{endpoint} {d}" for d in compare_shape(body, SHAPES / f"{endpoint}.json", optional=OPTIONAL)[:5]
            )
    assert not problems, f"{len(problems)} shape differences:\n" + "\n".join(problems)
