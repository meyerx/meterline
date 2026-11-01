"""B3-a: the connector READMEs say what state each provider's API is in, with a date, and the claims the provider's
documentation leaves open are marked unverified.

These are text checks: they fail when a statement goes missing or an old one returns. They cannot judge whether a
sentence is true; a reviewer does that against the code and the provider's documentation.

Run: python3 -m pytest -q tools/test_provider_status_docs.py
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def section(text, heading):
    match = re.search(r"^## %s\b.*?$(.*?)(?=^## |\Z)" % re.escape(heading), text, re.M | re.S)
    assert match, "no section '## %s'" % heading
    return match.group(1)


ANALYTICS = ("packages", "meterline-anthropic", "README.md")
ADMIN = ("packages", "meterline-anthropic-admin", "README.md")
COPILOT = ("packages", "meterline-copilot", "README.md")
LITELLM = ("packages", "meterline-litellm", "README.md")


# ---- Anthropic: the report endpoints are legacy ----

def test_both_anthropic_readmes_say_legacy_since_2026_10_29_with_no_sunset_date_and_the_successors():
    for path in (ANALYTICS, ADMIN):
        body = section(read(*path), "Provider status")
        assert "legacy" in body.lower(), path
        assert "2026-10-29" in body, path
        assert "no sunset date" in body, path
        assert "/v1/organizations/analytics/usage" in body and "/v1/organizations/analytics/cost" in body, path
        assert "analytics-usage-cost-2026-08-24" in body, path
        assert "stays on the legacy endpoints" in body, path
        assert "tracked separately" in body, path


def test_admin_readme_names_both_endpoints_it_calls_as_legacy():
    body = section(read(*ADMIN), "Provider status")
    assert "/v1/organizations/usage_report/messages" in body and "/v1/organizations/cost_report" in body


def test_analytics_readme_names_which_endpoints_are_legacy_and_which_are_not():
    body = section(read(*ANALYTICS), "Provider status")
    for name in ("user_usage_report", "user_cost_report", "usage_report", "cost_report"):
        assert "`%s`" % name in body, name
    assert "`users`" in body and "`summaries`" in body and "not legacy" in body


def test_analytics_readme_says_the_pinned_version_the_renames_and_the_plan():
    body = section(read(*ANALYTICS), "Provider status")
    assert "2023-06-01" in body and "anthropic-version" in body
    assert "2026-10-21" in body and "email_address" in body and "actor.email" in body and "`summaries`" in body
    assert "unverified" in body.lower()
    assert "Enterprise" in body and "plan" in body.lower()


# ---- Copilot ----

def test_copilot_readme_says_ga_with_a_date_and_marks_the_seats_endpoints_as_still_preview():
    text = read(*COPILOT)
    assert "public preview" not in text.lower()
    body = section(text, "Known limits")
    assert "2026-02-27" in body and "generally available" in body
    assert "seats" in body and "still marked preview" in body


def test_copilot_readme_states_policy_role_and_plan_prerequisites():
    body = section(read(*COPILOT), "Before you run it")
    assert "Copilot usage metrics" in body and "Enabled everywhere" in body
    assert "View Organization Copilot Metrics" in body and "View Enterprise Copilot Metrics" in body
    assert "organization owner" in body.lower() and "enterprise owner" in body.lower()
    assert "plan" in body.lower() and "unverified" in body.lower()


def test_copilot_readme_one_day_layout_names_the_fixture_test_and_that_no_live_report_was_seen():
    body = section(read(*COPILOT), "Known limits")
    assert "test_one_day_flat_layout_copilot" in body and "aggregate_1_day_row.ndjson" in body
    assert "not seen" not in body.replace("no live report has been seen", "")


# ---- LiteLLM ----

def test_litellm_readme_does_not_assert_that_prompt_tokens_includes_cache():
    text = read(*LITELLM)
    assert "prompt_tokens` includes the cache figures for Anthropic and Bedrock" not in text
    mapping = section(text, "Record mapping")
    assert "unverified" in mapping.lower() and "prompt_tokens" in mapping


def test_litellm_readme_says_where_requests_come_from():
    mapping = section(read(*LITELLM), "Record mapping")
    assert "spend log" in mapping and "upstream attempt" in mapping and "logged" in mapping


def test_litellm_provenance_is_dated_in_the_readme_and_in_the_shape_folder():
    shapes = read("docs", "api-shapes", "litellm", "README.md")
    assert "2026-10-29" in shapes and "v1.106.6" in shapes and "v1.104.2" in shapes
    assert "fake database" in shapes and "not cover the SQL" in shapes
    for name in ("has_more", "exclude_end_user_ids"):
        assert name in shapes, name
    readme = read(*LITELLM)
    assert "2026-10-29" in readme and "2026-10-11" in readme
    assert "2026-10-29" in read("docs", "api-shapes", "README.md")


# ---- the shape folders do not repeat an old statement ----

def test_anthropic_shape_readmes_say_which_endpoints_are_legacy():
    assert "legacy" in read("docs", "api-shapes", "anthropic-admin", "README.md").lower()
    assert "legacy" in read("docs", "api-shapes", "README.md").lower()
