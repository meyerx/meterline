"""MF-13: the public names are pinned, so a later rename fails a test.

Three literal lists, typed here and never read from the tree they check: the columns of every public schema in
docs/schemas (record schemas by sorted property name, tables by the exact order of x-columns), the key fields of
each record schema, and the flags of every command line of the console scripts listed below. A column or a flag added,
removed or renamed makes the test fail until this list is changed on purpose.

Run: python3 -m pytest -q tools/test_public_names_pinned.py
"""
import glob
import importlib
import json
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PINNED_COLUMNS = {
    "activity.schema.json": (
        "actor_email actor_id actor_kind actor_login batch_id commits counters data_refreshed_at edits_accepted "
        "edits_rejected grain lines_added lines_removed messages period_end period_start product provider "
        "provider_account_id pull_requests pulled_at record_version requests scope sessions suggestions_accepted "
        "suggestions_shown"
    ),
    "gold-audiences.schema.json": "description gold_schema_version rules",
    "gold-dataset-alert_events.schema.json": "schema_version day event_type scenario_id subject_kind subject_key value threshold trailing_sum trailing_days",
    "gold-dataset-comparison.schema.json": "schema_version month compare_cell own_cost_ref comparison_band hidden",
    "gold-dataset-comparison_groups.schema.json": "schema_version month compare_cell persons p50_cost_ref p75_cost_ref p90_cost_ref",
    "gold-dataset-costs_month.schema.json": (
        "schema_version month provider provider_account_id currency usage_cost_source cost_source_basis usage_cost_ref "
        "seat_cost total_cost_ref cost_basis_label invoice_usage"
    ),
    "gold-dataset-data_quality.schema.json": "schema_version month provider provider_account_id metric value",
    "gold-dataset-directory.schema.json": "schema_version person_key given_name family_name email",
    "gold-dataset-exec-costs_month.schema.json": (
        "schema_version month provider provider_account_id currency usage_cost_source cost_source_basis usage_cost_ref "
        "seat_cost total_cost_ref cost_basis_label invoice_usage hidden"
    ),
    "gold-dataset-exec-groups_month.schema.json": (
        "schema_version month cell_id cell_kind cell_key depth parent_cell_id persons active_persons active_days cost_ref "
        "p50_cost_ref p75_cost_ref p90_cost_ref p99_cost_ref top_decile_share is_final hidden"
    ),
    "gold-dataset-exec-pools_month.schema.json": (
        "schema_version month provider provider_account_id seats_billed included_per_seat pool_credits gross_credits "
        "pool_used_credits overage_credits overage_amount pool_used_share budget_amount budget_state budget_crossed_on "
        "currency is_final hidden"
    ),
    "gold-dataset-exec-scenario_summary.schema.json": "schema_version scenario_id month persons persons_stopped units hidden",
    "gold-dataset-exec-seats_month.schema.json": (
        "schema_version month provider provider_account_id seat_source eval_day seats_billed seats_on_eval_day "
        "active_seats idle_seats seats_reported_max seat_price seat_cost currency hidden"
    ),
    "gold-dataset-groups_month.schema.json": (
        "schema_version month cell_id cell_kind cell_key depth parent_cell_id suppressed suppression_reason persons "
        "active_persons active_days cost_ref p50_cost_ref p75_cost_ref p90_cost_ref p99_cost_ref top_decile_share "
        "is_final"
    ),
    "gold-dataset-identity_map.schema.json": "schema_version person_key provider method actor_kind actor_key valid_from valid_to",
    "gold-dataset-manager-groups_month.schema.json": (
        "schema_version month cell_id cell_kind cell_key depth parent_cell_id persons active_persons active_days cost_ref "
        "p50_cost_ref p75_cost_ref p90_cost_ref p99_cost_ref top_decile_share is_final hidden"
    ),
    "gold-dataset-named_exceptions.schema.json": "schema_version month rule_id audience person_key manager_key condition threshold value",
    "gold-dataset-people.schema.json": "schema_version person_key valid_from valid_to unit_id manager_key function level cost_center population attrs",
    "gold-dataset-person-person_month.schema.json": (
        "schema_version month person_key month_status attribution_day unit_id function level cost_center population "
        "employed_days active_days active_days_by_tool requests tokens_input_total tokens_output tokens_cache_read "
        "credits cost_ref cost_ref_by_provider rows_unpriced rows_unknown_model premium_share cache_rate intensity_band "
        "is_final projected_cost_ref"
    ),
    "gold-dataset-person_day.schema.json": (
        "schema_version day month provider provider_account_id person_key actor_key identity_status identity_method "
        "actor_kind product model model_tier requests tokens_input_uncached tokens_cache_read tokens_cache_write "
        "tokens_cache_write_5m tokens_cache_write_1h tokens_input_total tokens_output credits cost_source "
        "cost_source_basis cost_source_currency cost_ref cost_ref_status is_final source_records"
    ),
    "gold-dataset-person_day_activity.schema.json": (
        "schema_version day month provider provider_account_id person_key actor_key identity_status product sessions "
        "messages requests suggestions_shown suggestions_accepted lines_added lines_removed edits_accepted edits_rejected "
        "commits pull_requests source_records"
    ),
    "gold-dataset-person_month.schema.json": (
        "schema_version month person_key month_status attribution_day unit_id manager_key function level cost_center "
        "population employed_days active_days active_days_by_tool requests tokens_input_total tokens_output "
        "tokens_cache_read credits cost_ref cost_ref_by_provider rows_unpriced rows_unknown_model premium_share "
        "cache_rate intensity_band is_final projected_cost_ref"
    ),
    "gold-dataset-pools_month.schema.json": (
        "schema_version month provider provider_account_id seats_billed included_per_seat pool_credits gross_credits "
        "pool_used_credits overage_credits overage_amount pool_used_share budget_amount budget_state budget_crossed_on "
        "currency is_final"
    ),
    "gold-dataset-reconciliation_month.schema.json": (
        "schema_version month provider provider_account_id currency actor_cost_source org_cost_source org_cost_basis "
        "unallocated_cost_source unreconciled_share actor_credits org_credits unallocated_credits cost_ref "
        "ref_vs_source_gap ref_vs_source_share invoice_usage invoice_usage_gap invoice_seats invoice_seats_gap"
    ),
    "gold-dataset-scenario_persons.schema.json": "schema_version scenario_id month person_key cap cost_ref stopped_on excess top_ups top_up_total",
    "gold-dataset-scenario_summary.schema.json": (
        "schema_version scenario_id month persons persons_stopped persons_excess persons_top_up_total units units_stopped "
        "units_excess units_top_up_total"
    ),
    "gold-dataset-scenario_units.schema.json": "schema_version scenario_id month unit_id envelope position stopped_on excess top_ups top_up_total",
    "gold-dataset-seats_month.schema.json": (
        "schema_version month provider provider_account_id seat_source eval_day seats_billed seats_on_eval_day "
        "active_seats idle_seats seats_reported_max seat_price seat_cost currency"
    ),
    "gold-dataset-units.schema.json": "schema_version unit_id valid_from valid_to dimension key name kind parent_id function cost_center head_key",
    "gold-manifest.schema.json": (
        "as_of data_through generator gold_schema_version input_flags inputs key_check months outputs suppression "
        "unknown_envelope_keys"
    ),
    "gold-org-directory.schema.json": "person_id given_name family_name email",
    "gold-org-identities.schema.json": "person_id provider method actor_kind actor_id actor_login actor_email valid_from valid_to",
    "gold-org-memberships.schema.json": "person_id unit_id dimension role valid_from valid_to",
    "gold-org-people.schema.json": "person_id valid_from valid_to unit_id manager_id function level cost_center population attrs",
    "gold-org-units.schema.json": "unit_id valid_from valid_to dimension key name kind parent_id function cost_center head_person_id",
    "org_totals.schema.json": (
        "batch_id cost cost_basis cost_list currency data_refreshed_at extras grain is_final model model_provider "
        "period_end period_start product provider provider_account_id pulled_at quantities record_version requests scope "
        "tokens_cache_read tokens_cache_write tokens_cache_write_1h tokens_cache_write_5m tokens_input_total "
        "tokens_input_uncached tokens_output tokens_reasoning"
    ),
    "seat_assignments.schema.json": (
        "actor_email actor_id actor_kind actor_login assigned_at batch_id data_refreshed_at grain granted_by "
        "last_active_at last_authenticated_at pending_cancellation_date period_end period_start plan provider "
        "provider_account_id pulled_at record_version scope"
    ),
    "seats.schema.json": (
        "batch_id daily_active data_refreshed_at grain monthly_active pending_cancellations pending_invites period_end "
        "period_start provider provider_account_id pulled_at record_version scope seats weekly_active"
    ),
    "usage.schema.json": (
        "actor_deleted actor_email actor_id actor_kind actor_login batch_id cost cost_basis cost_list cost_only currency "
        "data_refreshed_at extras grain is_final model model_provider period_end period_start product provider "
        "provider_account_id pulled_at quantities record_version requests scope tokens_cache_read tokens_cache_write "
        "tokens_cache_write_1h tokens_cache_write_5m tokens_input_total tokens_input_uncached tokens_output "
        "tokens_reasoning"
    ),
}

PINNED_RECORD_KEYS = {
    "activity.schema.json": ('provider', 'provider_account_id', 'scope', 'grain', 'period_start', 'actor_kind', 'actor_id', 'product'),
    "org_totals.schema.json": ('provider', 'provider_account_id', 'scope', 'grain', 'period_start', 'product', 'model'),
    "seat_assignments.schema.json": ('provider', 'provider_account_id', 'scope', 'period_start', 'actor_id'),
    "seats.schema.json": ('provider', 'provider_account_id', 'scope', 'period_start'),
    "usage.schema.json": ('provider', 'provider_account_id', 'scope', 'grain', 'period_start', 'actor_kind', 'actor_id', 'product', 'model'),
}

# the flags of each command line, from its --help; the provider account is --provider-account-id (anthropic-admin) and
# the customer's organisation files are --org-files (gold build)
_COMMON = ["--data-dir", "--help", "--max-retry-after", "--parquet"]
PINNED_FLAGS = {
    ("meterline_anthropic.cli", "backfill"): _COMMON + ["--rate-per-minute", "--since"],
    ("meterline_anthropic.cli", "daily"): _COMMON + ["--rate-per-minute", "--since"],
    ("meterline_anthropic.cli", "rebuild"): _COMMON + ["--rate-per-minute"],
    ("meterline_anthropic_admin.cli", "backfill"): _COMMON + ["--provider-account-id", "--rate-per-minute", "--since", "--workspace-id"],
    ("meterline_anthropic_admin.cli", "daily"): _COMMON + ["--provider-account-id", "--rate-per-minute", "--since", "--workspace-id"],
    ("meterline_anthropic_admin.cli", "rebuild"): ["--data-dir", "--help", "--parquet"],
    ("meterline_copilot.cli", "backfill"): _COMMON + ["--level", "--rate-per-hour", "--since", "--slug"],
    ("meterline_copilot.cli", "daily"): _COMMON + ["--level", "--rate-per-hour", "--since", "--slug"],
    ("meterline_copilot.cli", "rebuild"): ["--data-dir", "--help", "--parquet"],
    ("meterline_litellm.cli", "backfill"): _COMMON + ["--end-users", "--proxy-name", "--rate-per-minute", "--since"],
    ("meterline_litellm.cli", "daily"): _COMMON + ["--end-users", "--proxy-name", "--rate-per-minute", "--since"],
    ("meterline_litellm.cli", "rebuild"): ["--data-dir", "--help", "--parquet"],
    ("meterline_gold.cli", "build"): ["--help", "--invoice", "--org-files", "--out", "--parquet", "--policy", "--prices", "--silver"],
    ("meterline_gold.cli", "schemas"): ["--help", "--out"],
    ("meterline_gold.cli", "import-litellm-prices"): ["--as-of", "--file", "--help", "--keys", "--out"],
    ("meterline_sim.cli", "generate"): ["--help", "--months", "--out", "--params", "--preset", "--scale", "--seed", "--start"],
}
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")


def schema(name):
    with open(os.path.join(ROOT, "docs", "schemas", name), encoding="utf-8") as fh:
        return json.load(fh)


def columns_of(name):
    doc = schema(name)
    return doc.get("x-columns") or sorted(doc["properties"])


def flags_of(module, sub):
    code = "import sys; from %s import main; main(sys.argv[1:])" % module
    out = subprocess.run([sys.executable, "-c", code, sub, "--help"], capture_output=True, text=True, check=False, cwd=ROOT).stdout
    return sorted(set(FLAG.findall(out)))


def test_every_public_schema_is_pinned():
    on_disk = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "docs", "schemas", "*.schema.json")))
    assert on_disk == sorted(PINNED_COLUMNS), "a schema was added or removed: pin it here"


@pytest.mark.parametrize("name", sorted(PINNED_COLUMNS))
def test_public_names_pinned_columns(name):
    assert list(columns_of(name)) == PINNED_COLUMNS[name].split(), name


@pytest.mark.parametrize("name", sorted(PINNED_RECORD_KEYS))
def test_public_names_pinned_record_keys(name):
    assert tuple(schema(name)["x-record-key"]) == PINNED_RECORD_KEYS[name], name


@pytest.mark.parametrize("module, sub", sorted(PINNED_FLAGS))
def test_public_names_pinned_flags(module, sub):
    importlib.import_module(module)
    assert flags_of(module, sub) == sorted(PINNED_FLAGS[(module, sub)]), "%s %s" % (module, sub)


def test_public_names_pinned_negative_control():
    # a column list that differs in one name is not equal to the pinned one, and a flag list likewise
    planted = PINNED_COLUMNS["usage.schema.json"].replace("provider_account_id", "org_id").split()
    assert planted != PINNED_COLUMNS["usage.schema.json"].split()
    assert sorted(["--org-id", "--help"]) != sorted(["--provider-account-id", "--help"])
