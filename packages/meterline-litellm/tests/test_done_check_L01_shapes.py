"""Done-check L01 (spec v2 section 8): the fake proxy's answers match the
shape files of ``docs/api-shapes/litellm/`` by keys and nesting (values are
never compared).

The requests are made by the connector's own client, so the check also shows
that what the connector sends is accepted by the fake."""

import copy
import json
from decimal import Decimal
from pathlib import Path

from meterline.conformance import compare_shape
from meterline_litellm.testing import end_row, row
from litellm_helpers import D1

SHAPES = Path(__file__).resolve().parents[3] / "docs" / "api-shapes" / "litellm"

# SpendMetrics fields the fake may leave out (spec L01); everything else is required.
REQUIRED_METRICS = {"spend", "prompt_tokens", "completion_tokens", "api_requests"}
# Maps whose keys are data: compared by the shape of their values.
DATA_KEYED = {
    "mcp_servers", "models", "model_groups", "providers", "endpoints", "api_keys", "entities", "api_key_breakdown",
}


def _metric_names():
    """The SpendMetrics fields the shape file lists; every one of them except REQUIRED_METRICS may be left out."""
    shape = json.loads((SHAPES / "user_daily_activity.json").read_text(encoding="utf-8"))
    return [name for name in shape["results"][0]["metrics"] if name not in REQUIRED_METRICS]


# "metrics.<field>": a key of that name directly inside a `metrics` map, and nowhere else.
OPTIONAL_METRICS = tuple("metrics." + name for name in _metric_names())


def differences(answer, shape_file):
    """The differences of ``answer`` from ``docs/api-shapes/litellm/<shape_file>``, or from a loaded shape."""
    shape = SHAPES / shape_file if isinstance(shape_file, str) else shape_file
    return compare_shape(answer, shape, data_keys=DATA_KEYED, optional=OPTIONAL_METRICS)


def _fake_answers(make_proxy, fake_clock, fake_env):
    """What the connector's own client receives from the fake proxy: (label, answer, shape file, has rows)."""
    from meterline_litellm.client import LitellmClient

    rows = [
        row(D1, "user-a", "model-a", prompt_tokens=10, completion_tokens=5, api_requests=1, spend=Decimal("0.01")),
        row(D1, None, "model-a", prompt_tokens=10, completion_tokens=5, api_requests=1, spend=Decimal("0.01")),
        row(D1, "user-a", None, prompt_tokens=1, api_requests=1),
    ]
    ends = [end_row(D1, "end-1", "model-a", prompt_tokens=10, api_requests=1, spend=Decimal("0.01"))]
    proxy = make_proxy(
        rows, end_rows=ends, end_aliases={"end-1": "client one"},
        users=[{"user_id": "user-a", "user_email": "ada@example.com"}],
    )
    day = {"start_date": D1, "end_date": D1, "page": "1", "page_size": "1000"}
    asked = {
        "user/daily/activity": ({**day}, "user_daily_activity.json", "results"),
        "customer/daily/activity": ({**day}, "customer_daily_activity.json", "results"),
        "user/list": ({"page": "1", "page_size": "100"}, "user_list.json", "users"),
        "per-user user/daily/activity": ({**day, "user_id": "user-a"}, "user_daily_activity.json", "results"),
    }
    found = []
    with LitellmClient(fake_env, base_url="https://proxy.example.com", transport=proxy.transport,
                       clock=fake_clock, sleep=fake_clock.sleep) as client:
        for label, (params, shape_file, rows_key) in sorted(asked.items()):
            # the last entry is a per-user call, as the connector sends it
            body = client.get(label.replace("per-user ", ""), params)
            found.append((label, body, shape_file, bool(body[rows_key])))
    return found


def test_done_check_L01_fake_answers_in_documented_shape(make_proxy, fake_clock, fake_env):
    problems = []
    for label, body, shape_file, has_rows in _fake_answers(make_proxy, fake_clock, fake_env):
        if not has_rows:
            problems.append("%s: no rows, nothing compared" % label)
        problems.extend("%s %s" % (label, d) for d in differences(body, shape_file)[:5])
    assert not problems, "%d shape differences:\n%s" % (len(problems), "\n".join(problems))


def test_a_planted_difference_in_the_fakes_answer_is_reported(make_proxy, fake_clock, fake_env):
    """Negative control: a key removed, a key added and a required metric removed are each reported with their path;
    an optional metric removed is not."""
    answers = {label: (body, shape_file) for label, body, shape_file, _ in _fake_answers(make_proxy, fake_clock, fake_env)}
    activity, shape_file = answers["user/daily/activity"]
    assert differences(activity, shape_file) == []

    removed = copy.deepcopy(activity)
    del removed["results"][0]["date"]
    assert differences(removed, shape_file) == ["$.results[0].date: missing"]

    added = copy.deepcopy(activity)
    added["results"][0]["invented_key"] = 1
    assert differences(added, shape_file) == ["$.results[0].invented_key: not in the documented shape"]

    required = copy.deepcopy(activity)
    del required["results"][0]["metrics"]["api_requests"]
    assert differences(required, shape_file) == ["$.results[0].metrics.api_requests: missing"]

    optional = copy.deepcopy(activity)
    left_out = [name for name in OPTIONAL_METRICS if name.split(".")[1] in optional["results"][0]["metrics"]]
    assert left_out, "the fake leaves no optional metric in: nothing to remove"
    for name in left_out:
        del optional["results"][0]["metrics"][name.split(".")[1]]
    assert differences(optional, shape_file) == []

    users, users_shape = answers["user/list"]
    wrong = copy.deepcopy(users)
    del wrong["users"][0]["user_email"]
    assert differences(wrong, users_shape) == ["$.users[0].user_email: missing"]
