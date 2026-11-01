"""v1 to v2 converter (spec v2 section 3.8 and the table of section 4).

All v1 lines are Anthropic's. ``convert_v1`` returns the line the migrated
``meterline-anthropic`` writes for the same fact, so a converted batch and a v2
``rebuild`` give the same bytes.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Union

from meterline.records import KINDS, RecordError
from meterline.writers import read_v1_lines, write_jsonl

V1_KINDS = ("usage", "activity", "seats", "org_totals")

_ACTIVITY_COUNTERS = ("conversations", "artifacts", "skills_used", "connectors_used", "web_searches")
_ACTIVITY_CORE = (
    "sessions", "messages", "commits", "pull_requests",
    "lines_added", "lines_removed", "edits_accepted", "edits_rejected",
)
# compatibility history: the v1 files call the provider account "org_id"; the v2 field is "provider_account_id"
_V1_ACCOUNT = "org_id"
_COMMON = ("provider", _V1_ACCOUNT, "period_start", "period_end", "pulled_at", "data_refreshed_at", "batch_id")


def _need(line: Dict[str, Any], names) -> None:
    for name in names:
        if name not in line:
            raise RecordError("v1 line: missing field " + name)


def _measures(line: Dict[str, Any]) -> Dict[str, Any]:
    _need(line, ("tokens_input", "tokens_output", "tokens_cache_write_5m", "tokens_cache_write_1h",
                 "tokens_cache_read", "requests", "web_search_requests", "cost", "cost_list",
                 "currency", "is_final"))
    uncached = line["tokens_input"]
    read = line["tokens_cache_read"]
    w5, w1 = line["tokens_cache_write_5m"], line["tokens_cache_write_1h"]
    has_cost = line["cost"] is not None or line["cost_list"] is not None
    return {
        "tokens_input_uncached": uncached,
        "tokens_cache_read": read,
        "tokens_cache_write": w5 + w1,
        "tokens_cache_write_5m": w5,
        "tokens_cache_write_1h": w1,
        "tokens_input_total": uncached + read + w5 + w1,
        "tokens_output": line["tokens_output"],
        "tokens_reasoning": None,
        "requests": line["requests"],
        "quantities": {},
        "cost": line["cost"],
        "cost_list": line["cost_list"],
        "currency": line["currency"],
        "cost_basis": "consumption" if has_cost else None,
        "extras": {"web_search_requests": line["web_search_requests"]},
        "is_final": line["is_final"],
        "model_provider": "anthropic",
    }


def convert_v1(line: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """Turn one v1 line (a dict) of ``kind`` (``usage``, ``activity``, ``seats``
    or ``org_totals``) into the v2 line of the same fact."""
    if kind not in V1_KINDS:
        raise RecordError("convert_v1: unknown v1 kind " + repr(kind))
    if "record_version" in line:
        raise RecordError("convert_v1: this line is already v2")
    _need(line, _COMMON)
    out: Dict[str, Any] = {("provider_account_id" if name == _V1_ACCOUNT else name): line[name] for name in _COMMON}
    out.update(record_version=2, scope="", grain="day")
    if kind == "usage":
        _need(line, ("actor_id", "actor_email", "actor_deleted", "product", "model", "cost_only"))
        out.update(_measures(line))
        out.update(
            actor_kind="user", actor_id=line["actor_id"], actor_email=line["actor_email"],
            actor_login=None, actor_deleted=line["actor_deleted"], product=line["product"],
            model=line["model"], cost_only=line["cost_only"],
        )
    elif kind == "org_totals":
        out.update(_measures(line))
        out.update(product="", model="")
    elif kind == "activity":
        _need(line, ("actor_id", "actor_email", "product") + _ACTIVITY_COUNTERS + _ACTIVITY_CORE)
        out.update(
            actor_kind="user", actor_id=line["actor_id"], actor_email=line["actor_email"],
            actor_login=None, product=line["product"],
            requests=None, suggestions_shown=None, suggestions_accepted=None,
            counters={name: line[name] for name in _ACTIVITY_COUNTERS},
        )
        out.update({name: line[name] for name in _ACTIVITY_CORE})
    else:
        _need(line, ("seats", "pending_invites", "daily_active", "weekly_active", "monthly_active"))
        out.update(
            seats=line["seats"], pending_invites=line["pending_invites"], pending_cancellations=None,
            daily_active=line["daily_active"], weekly_active=line["weekly_active"],
            monthly_active=line["monthly_active"],
        )
    return KINDS[kind].from_dict(out).to_dict()


def convert_v1_batch(path: Union[str, Path], kind: Optional[str] = None) -> Path:
    """Write ``<name>.v2.jsonl`` beside the v1 file ``path`` and return its
    path; the v1 file is not changed. ``kind`` defaults to the name of the
    folder the file is in (``records/<kind>/<batch_id>.jsonl``). Every converted
    record is validated; a line that fails raises ``RecordError``."""
    from meterline.records import validate

    source = Path(path)
    kind = kind or source.parent.name
    if kind not in V1_KINDS:
        raise RecordError("convert_v1_batch: cannot tell the kind of " + source.name)
    cls = KINDS[kind]
    records = []
    for line in read_v1_lines(source):
        record = cls.from_dict(convert_v1(line, kind))
        validate(record)
        records.append(record)
    target = source.with_name(source.stem + ".v2.jsonl")
    write_jsonl(records, target)
    return target
