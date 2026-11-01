"""F-02: the promise that prompt and response content never enters a record.

A record can only carry what its dataclass declares, so the declared fields
are held to a literal allowlist. A new field, a free-text one above all, fails
this test until a person adds it here on purpose and says in the review why it
is not content.
"""

import dataclasses
from typing import Dict, Set

from meterline.records import (
    KINDS,
    ActivityRecord,
    OrgTotal,
    SeatAssignment,
    SeatSnapshot,
    UsageRecord,
)

KEY_AND_BATCH = {
    "provider", "provider_account_id", "scope", "grain", "period_start", "period_end",
    "pulled_at", "data_refreshed_at", "batch_id", "record_version",
}
MEASURES = {
    "tokens_input_uncached", "tokens_cache_read", "tokens_cache_write", "tokens_cache_write_5m",
    "tokens_cache_write_1h", "tokens_input_total", "tokens_output", "tokens_reasoning", "requests",
    "quantities", "cost", "cost_list", "currency", "cost_basis", "extras",
}
ACTOR = {"actor_kind", "actor_id", "actor_email", "actor_login"}

ALLOWED_FIELDS: Dict[str, Set[str]] = {
    "UsageRecord": KEY_AND_BATCH | MEASURES | ACTOR | {
        "actor_deleted", "product", "model", "model_provider", "is_final", "cost_only"},
    "OrgTotal": KEY_AND_BATCH | MEASURES | {"product", "model", "model_provider", "is_final"},
    "ActivityRecord": KEY_AND_BATCH | ACTOR | {
        "product", "sessions", "messages", "requests", "suggestions_shown", "suggestions_accepted",
        "lines_added", "lines_removed", "edits_accepted", "edits_rejected", "commits", "pull_requests",
        "counters"},
    "SeatSnapshot": KEY_AND_BATCH | {
        "seats", "pending_invites", "pending_cancellations", "daily_active", "weekly_active", "monthly_active"},
    "SeatAssignment": KEY_AND_BATCH | ACTOR | {
        "plan", "assigned_at", "last_active_at", "last_authenticated_at", "pending_cancellation_date",
        "granted_by"},
}


def unexpected(cls) -> Dict[str, Set[str]]:
    """Fields of ``cls`` outside its allowlist, and allowlisted fields it lost."""
    have = {f.name for f in dataclasses.fields(cls)}
    want = ALLOWED_FIELDS.get(cls.__name__, set())
    return {"new": have - want, "gone": want - have}


def test_record_fields_allowlist():
    assert {cls.__name__ for cls in KINDS.values()} == set(ALLOWED_FIELDS), (
        "a record class was added or removed: list its fields in ALLOWED_FIELDS on purpose")
    for cls in (UsageRecord, OrgTotal, ActivityRecord, SeatSnapshot, SeatAssignment):
        diff = unexpected(cls)
        assert diff == {"new": set(), "gone": set()}, (
            "%s fields differ from the allowlist (%s): a record never carries prompt or response content; "
            "if the new field is not content, add it to ALLOWED_FIELDS" % (cls.__name__, diff))


def test_record_fields_allowlist_catches_a_new_free_text_field():
    """The check can fail: a subclass with a free-text field is reported."""
    leaky = dataclasses.make_dataclass("UsageRecord", [("prompt_text", str, dataclasses.field(default=""))],
                                       bases=(UsageRecord,), kw_only=True, frozen=True)
    assert unexpected(leaky)["new"] == {"prompt_text"}
