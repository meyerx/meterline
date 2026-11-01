"""Done-checks L02 to L09 (spec v2 section 8), through the command line and the
fake proxy, on synthetic data."""

import ast
import json
from decimal import Decimal
from pathlib import Path

from meterline_litellm.testing import PATH_A, PATH_B, PATH_C, end_row, row
from litellm_helpers import (
    D1, D2, NOW, at, backfill, basic_rows, daily, latest, manifests, records, sig, users,
)


def _by_actor(recs, kind="proxy_user"):
    return {(r.actor_id, r.period_start.isoformat(), r.model): r for r in recs if r.actor_kind == kind}


def test_done_check_L02_day_split_across_pages_sums(make_proxy, fake_clock, data_dir):
    rows = [
        row(D1, "user-a", "model-a", key="k1", prompt_tokens=100, completion_tokens=10, api_requests=1, spend=Decimal("0.10")),
        row(D1, "user-a", "model-a", key="k2", prompt_tokens=200, completion_tokens=20, api_requests=2, spend=Decimal("0.20")),
        row(D1, "user-a", "model-b", key="k1", prompt_tokens=50, completion_tokens=5, api_requests=1, spend=Decimal("0.05")),
    ]
    proxy = make_proxy(rows, users=users("user-a"), page_cap=2)
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0
    assert len(proxy.requests_for(PATH_A)) >= 4  # global and per-user, two pages each
    got = _by_actor(records(data_dir, "usage"))
    assert sorted(m for (_a, _d, m) in got) == ["model-a", "model-b"]
    rec = got[("user-a", D1, "model-a")]
    assert (rec.tokens_input_total, rec.tokens_output, rec.requests) == (300, 30, 3)
    assert rec.cost == Decimal("0.30")


def test_done_check_L03_input_buckets_and_anomaly(make_proxy, fake_clock, data_dir):
    rows = [
        row(D1, "user-a", "model-a", prompt_tokens=1000, completion_tokens=50, cache_read_input_tokens=600,
            cache_creation_input_tokens=300, api_requests=1, spend=Decimal("0.5")),
        row(D1, "user-b", "model-a", prompt_tokens=500, completion_tokens=5, cache_read_input_tokens=600,
            api_requests=1, spend=Decimal("0.1")),
    ]
    proxy = make_proxy(rows, users=users("user-a", "user-b"))
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0
    got = _by_actor(records(data_dir, "usage"))
    good = got[("user-a", D1, "model-a")]
    assert (good.tokens_input_uncached, good.tokens_input_total) == (100, 1000)
    assert (good.tokens_cache_read, good.tokens_cache_write) == (600, 300)
    bad = got[("user-b", D1, "model-a")]
    assert bad.tokens_input_uncached is None
    assert bad.tokens_input_total == 500
    found = manifests(data_dir)[-1]["token_anomalies"]
    assert len(found) == 1


def test_done_check_L04_unassigned_is_the_difference(make_proxy, fake_clock, data_dir):
    rows = [
        row(D1, "user-a", "model-a", prompt_tokens=100, completion_tokens=10, api_requests=2, spend=Decimal("0.40")),
        row(D1, "", "model-a", prompt_tokens=40, completion_tokens=4, api_requests=1, spend=Decimal("0.07")),
        row(D1, "", "model-b", prompt_tokens=15, completion_tokens=1, api_requests=1, spend=Decimal("0.03")),
        row(D2, "user-a", "model-a", prompt_tokens=60, completion_tokens=6, api_requests=1, spend=Decimal("0.20")),
        row(D2, "", "model-a", prompt_tokens=9, completion_tokens=3, api_requests=1, spend=Decimal("0.01")),
    ]
    proxy = make_proxy(rows, users=users("user-a"), unassigned_key="")
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0
    called = [e.params.get("user_id") for e in proxy.requests_for(PATH_A)]
    assert "" not in called  # no per-user call for the ownerless entity
    assert called.count("user-a") == 1
    usage = records(data_dir, "usage")
    un = {(r.period_start.isoformat(), r.model): r for r in usage if r.actor_kind == "unassigned"}
    assert set(un) == {(D1, "model-a"), (D1, "model-b"), (D2, "model-a")}
    assert all(r.actor_id == "(unassigned)" for r in un.values())
    assert (un[(D1, "model-a")].tokens_input_total, un[(D1, "model-a")].cost) == (40, Decimal("0.07"))
    assert (un[(D1, "model-b")].tokens_output, un[(D1, "model-b")].requests) == (1, 1)
    assert un[(D2, "model-a")].cost == Decimal("0.01")
    # proxy_user + unassigned equals the global totals to the cent
    for day, want in ((D1, Decimal("0.50")), (D2, Decimal("0.21"))):
        total = sum((r.cost for r in usage if r.period_start.isoformat() == day), Decimal(0))
        assert total == want


def test_done_check_L05_end_users_optional(make_proxy, fake_clock, tmp_path):
    rows = basic_rows()
    ends = [
        end_row(D1, "end-1", "model-a", prompt_tokens=70, completion_tokens=7, api_requests=1, spend=Decimal("0.04")),
        end_row(D1, "end-2", "model-b", prompt_tokens=30, completion_tokens=3, api_requests=2, spend=Decimal("0.02")),
        end_row(D2, "end-2", "model-b", prompt_tokens=10, completion_tokens=1, api_requests=1, spend=Decimal("0.01")),
    ]
    aliases = {"end-1": "client one", "end-2": "client two"}
    proxy = make_proxy(rows, users=users("user-a", "user-b"), end_rows=ends, end_aliases=aliases)
    off = tmp_path / "off"
    assert backfill(off, proxy, fake_clock, since="2026-09-15") == 0
    assert not proxy.requests_for(PATH_B)
    assert all(r.actor_kind != "end_user" for r in records(off, "usage"))

    on = tmp_path / "on"
    assert backfill(on, proxy, fake_clock, "2026-09-15", NOW, "--end-users") == 0
    calls = proxy.requests_for(PATH_B)
    assert len(calls) == 3  # one global, one per end user
    assert sorted(c.params.get("end_user_ids", "") for c in calls) == ["", "end-1", "end-2"]
    ends_out = [r for r in records(on, "usage") if r.actor_kind == "end_user"]
    got = {(r.actor_id, r.period_start.isoformat(), r.model): r for r in ends_out}
    assert set(got) == {("end-1", D1, "model-a"), ("end-2", D1, "model-b"), ("end-2", D2, "model-b")}
    assert got[("end-2", D1, "model-b")].tokens_input_total == 30
    assert got[("end-2", D1, "model-b")].requests == 2
    assert got[("end-1", D1, "model-a")].actor_login == "client one"
    assert got[("end-1", D1, "model-a")].actor_email is None
    proxy_off = sorted(json.dumps(sig(r), sort_keys=True) for r in records(off, "usage"))
    proxy_on = sorted(json.dumps(sig(r), sort_keys=True) for r in records(on, "usage") if r.actor_kind != "end_user")
    assert proxy_off == proxy_on
    assert all(r.actor_kind != "unassigned" or r.actor_id == "(unassigned)" for r in ends_out)


def test_done_check_L06_decimal_sums_and_no_float(make_proxy, fake_clock, data_dir):
    rows = [
        row(D1, "user-a", "model-a", key="k1", api_requests=1, spend=Decimal("0.1")),
        row(D1, "user-a", "model-a", key="k2", api_requests=1, spend=Decimal("0.2")),
    ]
    proxy = make_proxy(rows, users=users("user-a"), page_cap=1)
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0
    rec = _by_actor(records(data_dir, "usage"))[("user-a", D1, "model-a")]
    assert rec.cost == Decimal("0.30")
    assert type(rec.cost) is Decimal
    lines = (data_dir / "records" / "usage").glob("*.jsonl")
    text = "".join(p.read_text(encoding="utf-8") for p in lines)
    assert '"cost": "0.30"' in text

    # static scan: no float name, annotation, call or literal under packages/*/src
    src_files = sorted(Path(__file__).resolve().parents[2].glob("*/src/**/*.py"))
    assert any("meterline_litellm" in str(p) for p in src_files)
    offenders = []
    for path in src_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "float":
                offenders.append((path.name, node.lineno, "name float"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, float):
                offenders.append((path.name, node.lineno, "float literal"))
    assert offenders == []


def test_done_check_L07_daily_twice_then_a_revision(make_proxy, fake_clock, data_dir):
    rows = basic_rows() + [
        row("2026-09-19", "user-a", "model-a", prompt_tokens=80, completion_tokens=8, api_requests=1, spend=Decimal("0.11")),
    ]
    proxy = make_proxy(rows, users=users("user-a", "user-b"))
    assert daily(data_dir, proxy, fake_clock, NOW, "--since", "2026-09-01") == 0
    first = sorted(p.name for p in (data_dir / "records" / "usage").glob("*.jsonl"))
    assert len(first) == 1
    n_first = len(records(data_dir, "usage"))

    assert daily(data_dir, proxy, fake_clock, at("2026-09-20T07:00:00")) == 0
    assert sorted(p.name for p in (data_dir / "records" / "usage").glob("*.jsonl")) == first
    assert len(records(data_dir, "usage")) == n_first
    assert manifests(data_dir)[-1]["published_counts"]["usage"] == 0

    # a value inside the re-read window (the two days before the pull day) changes
    rows[-1]["spend"] = Decimal("0.19")
    rows[-1]["prompt_tokens"] = 95
    assert daily(data_dir, proxy, fake_clock, at("2026-09-20T08:00:00")) == 0
    files = sorted(p.name for p in (data_dir / "records" / "usage").glob("*.jsonl"))
    assert len(files) == 2
    new = latest(data_dir, "usage")
    key = next(k for k, r in new.items() if r.period_start.isoformat() == "2026-09-19")
    assert new[key].cost == Decimal("0.19")
    assert new[key].tokens_input_total == 95
    assert new[key].batch_id == files[-1].replace(".jsonl", "")
    assert len(records(data_dir, "usage")) == n_first + 1  # earlier batches stay readable


def test_done_check_L08_404_names_the_minimum_version(make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    proxy.missing_paths.add(PATH_A)
    code = daily(data_dir, proxy, fake_clock, NOW, "--since", "2026-09-01")
    assert code != 0
    err = capsys.readouterr().err
    assert "path not found: wrong base URL, or a proxy older than v1.67.0-stable" in err
    published = list((data_dir / "records").rglob("*.jsonl")) if (data_dir / "records").exists() else []
    assert published == []


def test_done_check_L09_only_active_users_are_called(make_proxy, fake_clock, data_dir):
    names = ["user-%02d" % i for i in range(50)]
    rows = [
        row(D1, name, "model-a", prompt_tokens=10 + i, completion_tokens=1, api_requests=1, spend=Decimal("0.01"))
        for i, name in enumerate(names[:12])
    ]
    proxy = make_proxy(rows, users=users(*names))
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0
    assert len(proxy.requests_for(PATH_A)) == 13
    asked = sorted(e.params["user_id"] for e in proxy.requests_for(PATH_A) if "user_id" in e.params)
    assert asked == names[:12]
    assert len(proxy.requests_for(PATH_C)) == 1
    assert proxy.requests_for(PATH_C)[0].params["page_size"] == "100"
