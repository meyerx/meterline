"""Done-check C01 (spec v2 section 8): for each endpoint A-E, what the fake API
returns, one users NDJSON row and one aggregate row match the files of
``docs/api-shapes/copilot/`` by keys and nesting (values are never compared).

The requests are made by the connector's own client, so the check also shows
that what the connector sends is accepted by the fake."""

import copy
import json
from decimal import Decimal
from pathlib import Path

from meterline.conformance import compare_shape
from meterline_copilot.testing import aggregate_row, item, seat, user_row
from copilot_helpers import FAKE_BILLING_TOKEN, SLUG

SHAPES = Path(__file__).resolve().parents[3] / "docs" / "api-shapes" / "copilot"

# fields a response may leave out (spec 6.2 A and D)
OPTIONAL_ROW = {
    "organization_id", "totals_by_cli", "totals_by_copilot_app", "totals_by_vscode_agent",
    "totals_by_3rd_party_agent", "used_vscode_agent",
    "distinct_custom_agent_use_count", "distinct_mcp_use_count", "distinct_plugin_use_count",
    "distinct_skill_use_count", "distinct_slash_cmd_use_count",
    "totals_by_custom_agent", "totals_by_mcp", "totals_by_plugin", "totals_by_skill", "totals_by_slash_cmd",
}
OPTIONAL_SEAT = {"plan_type", "assigning_team"}


def test_done_check_C01_fake_answers_in_documented_shape(make_api, fake_clock, fake_env, level):
    from meterline_copilot.client import CopilotClient

    api = make_api()
    day = "2026-09-18"
    api.users[day] = [
        user_row(day, 1, "login1", level=level, cli=True, app=True),
        user_row(day, 2, "login2", level=level, distinct=False),  # every optional field left out
    ]
    api.aggregates[day] = aggregate_row(day, 2, 3, 4)
    api.billing_day[day] = [item()]
    api.seats = [seat("octocat", 1), seat("login2", 2, plan=None, team=None)]

    problems = []

    def compare(label, shape_file, actual, optional=()):
        problems.extend("%s %s" % (label, d) for d in compare_shape(actual, SHAPES / shape_file, optional=optional)[:5])

    with CopilotClient(fake_env, billing_token=FAKE_BILLING_TOKEN, transport=api.transport,
                       clock=fake_clock, sleep=fake_clock.sleep, wall=fake_clock) as client:
        root = ("/orgs/" if level == "organization" else "/enterprises/") + SLUG
        broot = ("/organizations/" if level == "organization" else "/enterprises/") + SLUG
        agg = "organization-1-day" if level == "organization" else "enterprise-1-day"

        env = client.get_json(root + "/copilot/metrics/reports/users-1-day", {"day": day})
        compare("A envelope", "report_envelope.json", env)
        rows = [json.loads(line, parse_float=Decimal)
                for link in env["download_links"] for line in client.download(link).splitlines() if line.strip()]
        if len(rows) != 2:
            problems.append("A: expected 2 rows over the links, got %d" % len(rows))
        for row in rows:
            compare("A row", "users_1_day_row.ndjson", row, OPTIONAL_ROW)

        env = client.get_json(root + "/copilot/metrics/reports/" + agg, {"day": day})
        compare("B envelope", "report_envelope.json", env)
        agg_rows = [json.loads(line, parse_float=Decimal)
                    for link in env["download_links"] for line in client.download(link).splitlines() if line.strip()]
        if len(agg_rows) != 1:
            problems.append("B: expected 1 aggregate row, got %d" % len(agg_rows))
        for row in agg_rows:
            compare("B row", "aggregate_1_day_row.ndjson", row)

        name = "billing_ai_credit_usage_%s.json" % level
        body = client.get_json(broot + "/settings/billing/ai_credit/usage",
                               {"year": "2026", "month": "9", "day": "18"}, billing=True)
        compare("C", name, body)
        if not body["usageItems"]:
            problems.append("C: no items, nothing compared")

        body = client.get_json(root + "/copilot/billing/seats", {"page": "1", "per_page": "100"})
        compare("D", "seats.json", body, OPTIONAL_SEAT)
        if not body["seats"]:
            problems.append("D: no seats, nothing compared")
        if level == "organization":
            compare("E", "seat_summary.json", client.get_json(root + "/copilot/billing"))
    assert not problems, "%d shape differences:\n%s" % (len(problems), "\n".join(problems))


def test_a_planted_difference_in_the_fakes_answer_is_reported(make_api, fake_clock, fake_env, level):
    """Negative control: a key removed, a key added and a scalar turned into a list are each reported with their path;
    an optional key removed is not."""
    from meterline_copilot.client import CopilotClient

    api = make_api()
    day = "2026-09-18"
    api.users[day] = [user_row(day, 1, "login1", level=level, cli=True, app=True)]
    api.seats = [seat("octocat", 1)]
    with CopilotClient(fake_env, billing_token=FAKE_BILLING_TOKEN, transport=api.transport,
                       clock=fake_clock, sleep=fake_clock.sleep, wall=fake_clock) as client:
        root = ("/orgs/" if level == "organization" else "/enterprises/") + SLUG
        env = client.get_json(root + "/copilot/metrics/reports/users-1-day", {"day": day})
        lines = [line for link in env["download_links"] for line in client.download(link).splitlines() if line.strip()]
        seats = client.get_json(root + "/copilot/billing/seats", {"page": "1", "per_page": "100"})
    row = json.loads(lines[0], parse_float=Decimal)
    shape = SHAPES / "users_1_day_row.ndjson"
    assert compare_shape(row, shape, optional=OPTIONAL_ROW) == []
    assert OPTIONAL_ROW & set(row), "the fake leaves every optional key out: nothing to remove"

    removed = {k: v for k, v in row.items() if k != "user_login"}
    assert compare_shape(removed, shape, optional=OPTIONAL_ROW) == ["$.user_login: missing"]
    assert compare_shape({**row, "invented_key": 1}, shape, optional=OPTIONAL_ROW) == ["$.invented_key: not in the documented shape"]
    assert compare_shape({**row, "day": ["2026-09-18"]}, shape, optional=OPTIONAL_ROW) == ["$.day: expected scalar, got array"]
    assert compare_shape({k: v for k, v in row.items() if k not in OPTIONAL_ROW}, shape, optional=OPTIONAL_ROW) == []
    assert compare_shape({k: v for k, v in row.items() if k not in OPTIONAL_ROW}, shape) != []  # without the options, they are missing

    wrong = copy.deepcopy(seats)
    del wrong["seats"][0]["assignee"]
    assert compare_shape(wrong, SHAPES / "seats.json", optional=OPTIONAL_SEAT) == ["$.seats[0].assignee: missing"]
