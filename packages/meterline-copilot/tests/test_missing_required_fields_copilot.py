"""F-06: a required measure field that is missing or mistyped in a GitHub
response fails the run (exit 2, naming the endpoint and the field) and
publishes nothing. It is never dropped or counted as zero.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from meterline_copilot.testing import item, user_row
from copilot_helpers import daily, run

DAY = "2026-09-18"


def _no_records(data_dir):
    return not list(Path(data_dir).glob("records/*/*.jsonl"))


def _failed_manifests(data_dir):
    found = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((Path(data_dir) / "runs").glob("*.json"))]
    return [m for m in found if m["status"] == "failed"]


def _delete(row, path):
    *parents, leaf = path.split(".")
    node = row
    for name in parents:
        node = node[name]
    node.pop(leaf, None)


def _set(row, path, value):
    *parents, leaf = path.split(".")
    node = row
    for name in parents:
        node = node[name]
    node[leaf] = value


def _check(rc, data_dir, capsys, endpoint, field):
    err = capsys.readouterr().err
    assert rc == 2
    assert endpoint in err
    assert field in err
    assert "Traceback" not in err
    assert _no_records(data_dir)
    assert _failed_manifests(data_dir)


def test_missing_required_field_copilot_ai_credits_used(make_api, fake_clock, data_dir, level, capsys):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level, credits=None)]
    _check(daily(data_dir, api, fake_clock), data_dir, capsys, "users_1_day", "ai_credits_used")


@pytest.mark.parametrize(
    "block,path",
    [
        ("totals_by_cli", "token_usage.prompt_tokens_sum"),
        ("totals_by_cli", "token_usage.output_tokens_sum"),
        ("totals_by_cli", "request_count"),
        ("totals_by_copilot_app", "token_usage.prompt_tokens_sum"),
        ("totals_by_copilot_app", "token_usage.output_tokens_sum"),
        ("totals_by_copilot_app", "request_count"),
    ],
)
def test_missing_required_field_copilot_surface_block(block, path, make_api, fake_clock, data_dir, level, capsys):
    api = make_api()
    row = user_row(DAY, 1, "login1", level=level, cli=True, app=True)
    _delete(row[block], path)
    api.users[DAY] = [row]
    _check(daily(data_dir, api, fake_clock), data_dir, capsys, "users_1_day", path.split(".")[-1])


BILLING_FIELDS = ("grossQuantity", "grossAmount", "discountQuantity", "discountAmount", "netQuantity", "netAmount")


@pytest.mark.parametrize("field", BILLING_FIELDS)
def test_missing_required_field_copilot_billing_day(field, make_api, fake_clock, data_dir, level, capsys):
    api = make_api()
    entry = item()
    entry.pop(field)
    api.billing_day[DAY] = [entry]
    _check(daily(data_dir, api, fake_clock), data_dir, capsys, "billing_day", field)


@pytest.mark.parametrize("field", BILLING_FIELDS)
def test_missing_required_field_copilot_billing_user_month(field, make_api, fake_clock, data_dir, level, capsys):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level)]
    entry = item()
    entry.pop(field)
    api.billing_user[("login1", 2026, 9)] = [entry]
    _check(daily(data_dir, api, fake_clock), data_dir, capsys, "billing_user_month", field)


@pytest.mark.parametrize(
    "where,field,value",
    [
        ("credits", "ai_credits_used", "12.5"),
        ("credits", "ai_credits_used", None),
        ("surface", "token_usage.prompt_tokens_sum", "3800"),
        ("surface", "token_usage.output_tokens_sum", 1.5),
        ("surface", "request_count", None),
        ("billing", "grossAmount", "1.00"),
        ("billing", "grossQuantity", None),
        ("billing", "netAmount", True),
    ],
)
def test_missing_required_field_copilot_wrong_type(where, field, value, make_api, fake_clock, data_dir, level, capsys):
    api = make_api()
    row = user_row(DAY, 1, "login1", level=level, cli=True)
    if where == "credits":
        row[field] = value
    elif where == "surface":
        _set(row["totals_by_cli"], field, value)
    entry = item()
    if where == "billing":
        entry[field] = value
    api.users[DAY] = [row]
    api.billing_day[DAY] = [entry]
    _check(daily(data_dir, api, fake_clock), data_dir, capsys, "users_1_day" if where != "billing" else "billing_day",
           field.split(".")[-1])


def test_missing_required_field_copilot_optional_blocks_may_be_absent(make_api, fake_clock, data_dir, level):
    """totals_by_cli and totals_by_copilot_app are absent when unused (spec 6.2); that is not a failure."""
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level)]
    assert daily(data_dir, api, fake_clock) == 0


def test_rebuild_over_a_damaged_raw_file_exits_2_and_leaves_records(make_api, fake_clock, data_dir, level, capsys):
    import gzip

    from copilot_helpers import backfill

    day = "2026-09-18"
    api = make_api()
    api.users[day] = [user_row(day, 2, "login2", level=level)]
    api.billing_day[day] = [item()]
    assert backfill(data_dir, api, fake_clock) == 0
    before = {str(p.relative_to(data_dir)): p.read_bytes() for p in (data_dir / "records").rglob("*.jsonl")}
    assert before
    for raw in sorted((data_dir / "raw" / "billing_day").rglob("*.json.gz")):
        envelope = json.loads(gzip.decompress(raw.read_bytes()).decode("utf-8"))
        for page in envelope["pages"]:
            body = json.loads(page["body"])
            body["usageItems"] = [{key: (None if isinstance(value, (int, float)) and not isinstance(value, bool) else value)
                                   for key, value in entry.items()} for entry in body["usageItems"]]
            page["body"] = json.dumps(body)
        raw.write_bytes(gzip.compress(json.dumps(envelope).encode("utf-8")))
    capsys.readouterr()

    assert run(["rebuild", "--data-dir", str(data_dir)], api, fake_clock) == 2
    err = capsys.readouterr().err
    assert "rebuild:" in err and "20260920T060000Z" in err
    assert "Traceback" not in err and len(err.strip().splitlines()) == 1
    after = {str(p.relative_to(data_dir)): p.read_bytes() for p in (data_dir / "records").rglob("*.jsonl")}
    assert after == before
    assert not (data_dir / ".rebuild").exists()
