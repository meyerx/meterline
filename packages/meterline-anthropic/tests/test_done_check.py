"""Done-checks 01 to 03 (client level, raw result rows) and 07, 08, 10
(through fetch and normalise)."""

import ast
import gzip
import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from meterline_anthropic.testing import FakeApi
from meterline.records import UsageRecord, latest_per_key
from meterline.writers import read_jsonl, write_jsonl
from meterline_anthropic.cli import main
from meterline_anthropic.client import AnalyticsClient
from meterline_anthropic.fetch import RunManifest, batch_id_for, fetch_usage_window, windows
from meterline_anthropic.normalise import load_published, normalise_run

ENDPOINT = "user_usage_report"


def _params(start, end):
    return {
        "starting_at": start,
        "ending_at": end,
        "bucket_width": "1d",
        "group_by[]": ["model", "product"],
        "limit": "1000",
    }


def _rows(pages):
    return [row for body in pages for row in body["data"]]


def _keyed_rows(pages):
    """(actor, day, product, model) keys; the day comes from the row."""
    keys = []
    for body in pages:
        for row in body["data"]:
            keys.append((row["actor"]["user_id"], row["starting_at"][:10], row["product"], row["model"]))
    return keys


def _client(api, clock, key, **kw):
    kw.setdefault("jitter", lambda: 0)
    return AnalyticsClient(key, transport=api.transport, clock=clock, sleep=clock.sleep, **kw)


def test_done_check_01_three_pages_load_completely(dataset, fake_clock, fake_key_env):
    # Keep exactly 2,500 synthetic usage rows (a prefix of the fixture rows).
    def shrink(data):
        kept = 0
        for day in sorted(data["usage"]):
            rows = data["usage"][day]
            room = 2500 - kept
            data["usage"][day] = rows[:room]
            kept += len(data["usage"][day])
        assert kept == 2500

    api = FakeApi(dataset, page_limit=1000, clock=fake_clock, api_key=fake_key_env)
    api.mutate(shrink)
    with _client(api, fake_clock, fake_key_env) as client:
        pages = list(client.pages(ENDPOINT, _params("2026-07-01T00:00:00Z", "2026-10-10T00:00:00Z")))
    assert len(api.requests_for(ENDPOINT)) == 3
    assert [e.page for e in api.requests_for(ENDPOINT)] == [1, 2, 3]
    assert len(_rows(pages)) == 2500
    keys = _keyed_rows(pages)
    assert len(set(keys)) == 2500


def test_done_check_02_410_restarts_window(dataset, fake_clock, fake_key_env):
    params = _params("2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z")

    clean_api = FakeApi(dataset, page_limit=1000, clock=fake_clock, api_key=fake_key_env)
    with _client(clean_api, fake_clock, fake_key_env) as client:
        clean = _rows(list(client.pages(ENDPOINT, params)))
    assert len(clean) > 1000  # the window really has a page 2

    faulted_api = FakeApi(dataset, page_limit=1000, clock=fake_clock, api_key=fake_key_env)
    faulted_api.expire_cursor_on_page(ENDPOINT, 2)
    with _client(faulted_api, fake_clock, fake_key_env) as client:
        got = _rows(list(client.pages(ENDPOINT, params)))

    log = faulted_api.requests_for(ENDPOINT)
    n_pages = len(clean_api.requests_for(ENDPOINT))
    assert n_pages >= 2
    expected = [(1, 200), (2, 410)] + [(n, 200) for n in range(1, n_pages + 1)]
    assert [(e.page, e.status) for e in log] == expected
    assert got == clean


def test_done_check_03_rate_limit_and_retry_after(dataset, fake_clock, fake_key_env):
    api = FakeApi(dataset, page_limit=1000, clock=fake_clock, api_key=fake_key_env)
    params = _params("2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z")
    start = fake_clock()
    with _client(api, fake_clock, fake_key_env, rate_per_minute=50) as client:
        for _ in range(300):
            client.get(ENDPOINT, params)
    times = [e.time for e in api.log]
    assert len(times) == 300
    for i, t in enumerate(times):
        in_window = [u for u in times if t <= u <= t + 60]
        assert len(in_window) <= 50
    # throttling happened on the fake clock only: it advanced, nothing really slept
    assert fake_clock() - start >= 5 * 60 - 60
    assert fake_clock.sleeps

    # a scripted 429 with retry-after is honoured
    api2 = FakeApi(dataset, page_limit=1000, clock=fake_clock, api_key=fake_key_env)
    api2.fail_page(ENDPOINT, 1, 429, retry_after=17)
    with _client(api2, fake_clock, fake_key_env) as client:
        body = client.get(ENDPOINT, params)
    assert body["data"]
    first, second = api2.log
    assert (first.status, second.status) == (429, 200)
    assert second.time - first.time >= 17


PULLED_AT = datetime(2026, 10, 9, 13, 0, 0, tzinfo=timezone.utc)


def _pull_and_normalise(api, clock, key, data_dir, first, last, published=None, **kw):
    """Real fetch and normalise path through the fake API; returns the run."""
    files = []
    with _client(api, clock, key, **kw) as client:
        for w_first, w_last in windows(first, last):
            files.extend(p.path for p in fetch_usage_window(client, data_dir, w_first, w_last, PULLED_AT))
    return normalise_run(files, batch_id_for(PULLED_AT), PULLED_AT, published=published)


def test_done_check_07_cost_is_decimal(dataset, fake_clock, fake_key_env, data_dir, tmp_path):
    api = FakeApi(dataset, clock=fake_clock, api_key=fake_key_env)
    run = _pull_and_normalise(api, fake_clock, fake_key_env, data_dir, date(2026, 9, 15), date(2026, 9, 15))
    hits = [
        r for r in run.usage
        if r.actor_id == "user-001" and r.period_start == date(2026, 9, 15)
        and r.product == "claude_code" and r.model == "model-a-large"
    ]
    assert len(hits) == 1
    assert hits[0].cost == Decimal("412.80")
    assert type(hits[0].cost) is Decimal

    out = tmp_path / "usage.jsonl"
    write_jsonl(hits, out)
    line = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert line["cost"] == "412.80"
    assert '"cost": "412.80"' in out.read_text(encoding="utf-8")

    # static scan: no float name, annotation, call or literal under packages/*/src
    src_files = sorted(Path(__file__).resolve().parents[2].glob("*/src/**/*.py"))
    assert src_files
    offenders = []
    for path in src_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "float":
                offenders.append((path.name, node.lineno, "name float"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, float):
                offenders.append((path.name, node.lineno, "float literal"))
    assert offenders == []


def test_done_check_08_unknown_product_and_model_kept(dataset, fake_clock, fake_key_env, data_dir, tmp_path):
    day = "2026-09-15"
    actor = {"type": "user_actor", "user_id": "user-001", "email_address": "alex.archer@example.com", "deleted": False}
    base = {"actor": actor, "product": "new_product_x", "model": "model-y-preview"}
    usage_row = dict(base, uncached_input_tokens=10, output_tokens=5,
                     cache_creation={"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
                     cache_read_input_tokens=0, requests=1, server_tool_use={"web_search_requests": 0})
    cost_row = dict(base, amount="125.000000", list_amount="125.000000", currency="USD")

    def add_row(data):
        data["usage"][day].append(usage_row)
        data["cost"][day].append(cost_row)

    # a first batch is published; the second pull adds the new product and model
    first_api = FakeApi(dataset, clock=fake_clock, api_key=fake_key_env)
    first = _pull_and_normalise(first_api, fake_clock, fake_key_env, data_dir, date(2026, 9, 15), date(2026, 9, 15))
    assert first.unknown_products and first.unknown_models  # nothing published yet: every value is new
    write_jsonl(first.usage, data_dir / "records" / "usage" / (batch_id_for(PULLED_AT) + ".jsonl"))
    published = load_published(data_dir)

    api = FakeApi(dataset, clock=fake_clock, api_key=fake_key_env)
    api.mutate(add_row)
    run = _pull_and_normalise(
        api, fake_clock, fake_key_env, tmp_path / "second", date(2026, 9, 15), date(2026, 9, 15),
        published=published,
    )
    new = [r for r in run.usage if r.product == "new_product_x"]
    assert len(new) == 1
    assert new[0].model == "model-y-preview"
    assert new[0].cost == Decimal("1.25")
    assert run.unknown_products == ["new_product_x"]
    assert run.unknown_models == ["model-y-preview"]

    manifest = RunManifest(
        command="daily", batch_id=batch_id_for(PULLED_AT), pulled_at=PULLED_AT,
        unknown_products=run.unknown_products, unknown_models=run.unknown_models,
    )
    written = json.loads(manifest.to_json())
    assert written["unknown_products"] == ["new_product_x"]
    assert written["unknown_models"] == ["model-y-preview"]
    assert json.loads(manifest.write(data_dir).read_text(encoding="utf-8")) == written


def test_done_check_10_totals_match_fixture_to_the_cent(dataset, fake_clock, fake_key_env, data_dir, expected_totals):
    api = FakeApi(dataset, clock=fake_clock, api_key=fake_key_env)
    run = _pull_and_normalise(api, fake_clock, fake_key_env, data_dir, date(2026, 7, 1), date(2026, 10, 9))
    sums = {}
    for rec in run.usage:
        if rec.cost is None:  # the two cost-less rows contribute nothing, as in the generator
            continue
        by_model = sums.setdefault(rec.actor_id, {})
        by_model[rec.model] = by_model.get(rec.model, Decimal(0)) + rec.cost
    expected = {
        actor: {model: Decimal(text) for model, text in models.items()}
        for actor, models in expected_totals["cost_by_actor_model"].items()
    }
    assert sums == expected
    assert len(run.usage) == len({r.key() for r in run.usage})


# ---- done-checks 04, 05, 06, 09, 11 (through the command line) -------------------


def _utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


def _cli(api, clock, data_dir, command, *extra, now):
    if command == "daily" and "--since" not in extra and not (Path(data_dir) / "state.json").exists():
        # F-11: a first daily needs --since; these checks mean the first of the month, where daily used to start
        extra = (*extra, "--since", now.replace(day=1).date().isoformat())
    argv = [command, *extra, "--data-dir", str(data_dir)]
    return main(
        argv, transport=api.transport, clock=clock, sleep=clock.sleep,
        now=lambda: now, jitter=lambda: 0,
    )


def _read_kind(data_dir, kind, cls):
    out = []
    folder = Path(data_dir) / "records" / kind
    for path in sorted(folder.glob("*.jsonl")):
        out.extend(read_jsonl(path, cls))
    return out


def _hash_tree(root):
    root = Path(root)
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _bump_one_usage_row(api, day):
    """Change tokens and cost of one usage row that has a cost row; returns
    the record key fields."""
    changed = {}

    def bump(data):
        costs = {
            (r["actor"]["user_id"], r["product"], r["model"]): r for r in data["cost"][day]
        }
        for row in data["usage"][day]:
            match = costs.get((row["actor"]["user_id"], row["product"], row["model"]))
            if match is not None:
                row["uncached_input_tokens"] += 7
                match["amount"] = str(Decimal(match["amount"]) + Decimal(100))
                changed["key"] = (row["actor"]["user_id"], row["product"], row["model"])
                return
        raise AssertionError("no usage row with a cost row")

    api.mutate(bump)
    return changed["key"]


def test_done_check_04_activity_400_ends_window_cleanly(fake_api, fake_clock, data_dir):
    latest = date(2026, 10, 3)
    fake_api.set_latest_activity_date(latest)
    assert _cli(fake_api, fake_clock, data_dir, "daily", now=_utc(2026, 10, 5, 6)) == 0

    from meterline.records import ActivityRecord

    days = {r.period_start for r in _read_kind(data_dir, "activity", ActivityRecord)}
    assert days and max(days) == latest
    assert date(2026, 10, 4) not in days
    state = json.loads((data_dir / "state.json").read_text(encoding="utf-8"))
    assert state["activity_loaded"][-1] == "2026-10-03"
    assert state["latest_activity_date"] == "2026-10-03"
    assert state["pending"] is None

    seen = len(fake_api.log)
    fake_api.set_latest_activity_date(date(2026, 10, 4))
    assert _cli(fake_api, fake_clock, data_dir, "daily", now=_utc(2026, 10, 5, 9)) == 0
    asked = [e.params["date"] for e in fake_api.log[seen:] if e.endpoint == "users"]
    assert asked == ["2026-10-04"]
    days = {r.period_start for r in _read_kind(data_dir, "activity", ActivityRecord)}
    assert max(days) == date(2026, 10, 4)
    state = json.loads((data_dir / "state.json").read_text(encoding="utf-8"))
    assert state["activity_loaded"][-1] == "2026-10-04"


def test_done_check_05_revision_new_batch_latest_wins(fake_api, fake_clock, data_dir):
    first_at, second_at = _utc(2026, 10, 5, 6), _utc(2026, 10, 5, 18)
    assert _cli(fake_api, fake_clock, data_dir, "daily", now=first_at) == 0
    usage_dir = data_dir / "records" / "usage"
    old_files = _hash_tree(data_dir / "records")
    old_file = usage_dir / "20261005T060000Z.jsonl"
    assert old_file.is_file()
    old_rows = {r.key(): r for r in read_jsonl(old_file, UsageRecord)}
    assert all(r.is_final is False for r in old_rows.values() if r.period_start.month == 9)

    actor, product, model = _bump_one_usage_row(fake_api, "2026-09-15")
    assert _cli(fake_api, fake_clock, data_dir, "daily", now=second_at) == 0

    new_file = usage_dir / "20261005T180000Z.jsonl"
    assert new_file.is_file()
    changed = read_jsonl(new_file, UsageRecord)
    assert len(changed) == 1
    rec = changed[0]
    assert (rec.actor_id, rec.product, rec.model) == (actor, product, model)
    assert rec.period_start == date(2026, 9, 15)
    assert rec.batch_id == "20261005T180000Z"
    before = old_rows[rec.key()]
    assert rec.tokens_input_uncached == before.tokens_input_uncached + 7
    assert rec.cost == before.cost + Decimal(1)

    latest = latest_per_key(_read_kind(data_dir, "usage", UsageRecord))
    assert latest[rec.key()] == rec
    assert len(latest) == len(old_rows)
    # the old batch file still reads back with the old values and is untouched
    assert {r.key(): r for r in read_jsonl(old_file, UsageRecord)}[rec.key()] == before
    now_files = _hash_tree(data_dir / "records")
    for name, digest in old_files.items():
        assert now_files[name] == digest
    assert set(now_files) - set(old_files) == {"usage/20261005T180000Z.jsonl"}


def test_done_check_06_daily_twice_no_new_records(fake_api, fake_clock, data_dir):
    assert _cli(fake_api, fake_clock, data_dir, "daily", now=_utc(2026, 10, 5, 6)) == 0
    files = _hash_tree(data_dir / "records")
    count = sum(len(read_jsonl(data_dir / "records" / n, _cls_for(n))) for n in files)
    assert count > 0
    assert _cli(fake_api, fake_clock, data_dir, "daily", now=_utc(2026, 10, 5, 7)) == 0
    assert _hash_tree(data_dir / "records") == files
    manifest = json.loads((data_dir / "runs" / "20261005T070000Z.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "published"
    assert manifest["published_counts"] and set(manifest["published_counts"].values()) == {0}


def _cls_for(relative_name):
    from meterline_anthropic.normalise import KINDS

    return KINDS[relative_name.split("/", 1)[0]]


def test_done_check_09_rebuild_is_byte_identical(fake_api, fake_clock, data_dir):
    assert _cli(fake_api, fake_clock, data_dir, "backfill", "--since", "2026-07-01", now=_utc(2026, 10, 5, 6)) == 0
    _bump_one_usage_row(fake_api, "2026-09-15")
    assert _cli(fake_api, fake_clock, data_dir, "daily", now=_utc(2026, 10, 5, 18)) == 0
    before = _hash_tree(data_dir / "records")
    assert len(before) > 4
    assert any(name.endswith("20261005T180000Z.jsonl") for name in before)

    assert main(["rebuild", "--data-dir", str(data_dir)]) == 0
    after = _hash_tree(data_dir / "records")
    assert after == before
    assert not (data_dir / ".rebuild").exists()


def test_done_check_11_repo_scan_clean():
    root = Path(__file__).resolve().parents[3]
    skip_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".hypothesis", "exports"}
    key_pattern = re.compile("sk" + "-ant-" + r"[A-Za-z0-9_-]{6,}")
    email_pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
    # F-02: a 12-digit account id or a 16-digit card-like number; the two runs the repo
    # holds on purpose are money-format examples (docs/records.md, gold's 00_macros.sql)
    digit_pattern = re.compile(r"(?<![\w.])(?:\d{12}|\d{16})(?![\w.])")
    allowed_digit_runs = {"000000000001", "100000000000"}
    scanned = 0
    key_hits, email_hits, digit_hits = [], [], []
    for current, dirs, names in __import__("os").walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.endswith(".egg-info")]
        for name in names:
            path = Path(current) / name
            data = path.read_bytes()
            if name.endswith(".gz"):
                try:
                    data = gzip.decompress(data)
                except OSError:
                    pass
            text = data.decode("utf-8", errors="ignore")
            scanned += 1
            rel = path.relative_to(root).as_posix()
            if key_pattern.search(text):
                key_hits.append(rel)
            for token in email_pattern.findall(text):
                if not token.lower().endswith("@example.com"):
                    email_hits.append((rel, token))
            for token in digit_pattern.findall(text):
                if token not in allowed_digit_runs:
                    digit_hits.append((rel, token))
    assert scanned > 10
    assert key_hits == []
    assert email_hits == []
    assert digit_hits == []

    marker = root / "packages" / "meterline-anthropic" / "tests" / "fixtures" / "data" / "SYNTHETIC.json"
    assert json.loads(marker.read_text(encoding="utf-8"))["synthetic"] is True
    assert (root / "packages" / "meterline-anthropic" / "tests" / "fixtures" / "generate.py").is_file()


# ---- done-checks 13 to 17 (spec v1.1 section 7), against the documented shapes -----


def _chat_block(**over):
    block = {
        "distinct_conversation_count": 0, "message_count": 0, "distinct_artifacts_created_count": 0,
        "distinct_skills_used_count": 0, "distinct_connectors_used_count": 0,
    }
    block.update(over)
    return block


def _cowork_block(**over):
    block = {
        "distinct_session_count": 0, "message_count": 0, "artifacts_created_count": 0,
        "distinct_skills_used_count": 0, "distinct_connectors_used_count": 0,
    }
    block.update(over)
    return block


def _app_block(**over):
    block = {
        "distinct_session_count": 0, "message_count": 0,
        "distinct_skills_used_count": 0, "distinct_connectors_used_count": 0,
    }
    block.update(over)
    return block


def _activity_row(user_id, chat=None, code=None, cowork=None, excel=None):
    """A complete `users` row in the documented shape; blocks default to zero."""
    tools = {
        name: {"accepted_count": 0, "rejected_count": 0}
        for name in ("edit_tool", "multi_edit_tool", "notebook_edit_tool", "write_tool")
    }
    core = {
        "distinct_session_count": 0, "commit_count": 0, "pull_request_count": 0,
        "artifacts_created_count": 0, "lines_of_code": {"added_count": 0, "removed_count": 0},
    }
    if code:
        core.update(code.get("core", {}))
        tools.update(code.get("tools", {}))
    return {
        "user": {"type": "user", "id": user_id, "email_address": user_id + "@example.com"},
        "chat_metrics": _chat_block(**(chat or {})),
        "claude_code_metrics": {"core_metrics": core, "tool_actions": tools},
        "cowork_metrics": _cowork_block(**(cowork or {})),
        "design_metrics": {"distinct_session_count": None, "message_count": 0},
        "office_metrics": {
            "excel": _app_block(**(excel or {})),
            "outlook": _app_block(), "powerpoint": _app_block(), "word": _app_block(),
        },
        "science_metrics": {"distinct_session_count": None, "message_count": 0},
        "web_search_count": 0,
    }


def _all_records(data_dir):
    from meterline.records import ActivityRecord, OrgTotal, SeatSnapshot

    return {
        "usage": _read_kind(data_dir, "usage", UsageRecord),
        "activity": _read_kind(data_dir, "activity", ActivityRecord),
        "seats": _read_kind(data_dir, "seats", SeatSnapshot),
        "org_totals": _read_kind(data_dir, "org_totals", OrgTotal),
    }


def _manifest(data_dir, batch_id):
    return json.loads((Path(data_dir) / "runs" / (batch_id + ".json")).read_text(encoding="utf-8"))


def test_done_check_13_user_row_yields_one_record_per_product_block(
    dataset, fake_clock, fake_key_env, data_dir
):
    row = _activity_row(
        "user-t13",
        chat={"distinct_conversation_count": 4, "message_count": 20,
              "distinct_artifacts_created_count": 2, "distinct_skills_used_count": 1,
              "distinct_connectors_used_count": 2},
        code={
            "core": {"distinct_session_count": 3, "commit_count": 2, "pull_request_count": 1,
                     "artifacts_created_count": 1,
                     "lines_of_code": {"added_count": 120, "removed_count": 30}},
            "tools": {"edit_tool": {"accepted_count": 5, "rejected_count": 1},
                      "write_tool": {"accepted_count": 2, "rejected_count": 3}},
        },
        cowork={"distinct_session_count": 2, "message_count": 9, "artifacts_created_count": 1,
                "distinct_skills_used_count": 1, "distinct_connectors_used_count": 1},
        excel={"distinct_session_count": 1, "message_count": 7, "distinct_connectors_used_count": 1},
    )
    dataset["activity"]["2026-10-02"] = [row]
    api = FakeApi(dataset, clock=fake_clock, api_key=fake_key_env)
    assert _cli(api, fake_clock, data_dir, "daily", now=_utc(2026, 10, 5, 6)) == 0

    from meterline.records import ActivityRecord

    got = [
        r for r in _read_kind(data_dir, "activity", ActivityRecord)
        if r.actor_id == "user-t13" and r.period_start == date(2026, 10, 2)
    ]
    assert len(got) == 4
    by_product = {r.product: r for r in got}
    assert set(by_product) == {"claude_code", "chat", "cowork", "office_agent.excel"}
    zero = {name: 0 for name in (
        "sessions", "conversations", "messages", "commits", "pull_requests", "lines_added",
        "lines_removed", "edits_accepted", "edits_rejected", "artifacts", "skills_used",
        "connectors_used", "web_searches",
    )}
    expected = {
        "claude_code": dict(zero, sessions=3, commits=2, pull_requests=1, lines_added=120,
                            lines_removed=30, edits_accepted=7, edits_rejected=4, artifacts=1),
        "chat": dict(zero, conversations=4, messages=20, artifacts=2, skills_used=1, connectors_used=2),
        "cowork": dict(zero, sessions=2, messages=9, artifacts=1, skills_used=1, connectors_used=1),
        "office_agent.excel": dict(zero, sessions=1, messages=7, connectors_used=1),
    }
    counter_names = ("conversations", "artifacts", "skills_used", "connectors_used", "web_searches")
    for product, numbers in expected.items():
        rec = by_product[product]
        got = {name: (rec.counters[name] if name in counter_names else getattr(rec, name)) for name in zero}
        assert got == numbers, product
        assert rec.actor_email == "user-t13@example.com"


def test_done_check_14_2500_users_over_three_next_page_pages(dataset, fake_clock, fake_key_env, data_dir):
    rows = [
        _activity_row("user-t14-%04d" % i, chat={"distinct_conversation_count": 1, "message_count": 1})
        for i in range(2500)
    ]
    dataset["activity"]["2026-10-02"] = rows
    api = FakeApi(dataset, page_limit=1000, clock=fake_clock, api_key=fake_key_env)
    assert _cli(api, fake_clock, data_dir, "daily", now=_utc(2026, 10, 5, 6)) == 0

    asked = [e for e in api.requests_for("users") if e.params.get("date") == "2026-10-02"]
    assert [(e.page, e.status) for e in asked] == [(1, 200), (2, 200), (3, 200)]

    from meterline.records import ActivityRecord

    loaded = {
        r.actor_id for r in _read_kind(data_dir, "activity", ActivityRecord)
        if r.period_start == date(2026, 10, 2)
    }
    assert loaded == {"user-t14-%04d" % i for i in range(2500)}


def test_done_check_15_missing_refresh_stamps_do_not_stop_the_run(
    dataset, fake_clock, fake_key_env, data_dir
):
    api = FakeApi(dataset, clock=fake_clock, api_key=fake_key_env)
    # users and summaries never carry data_refreshed_at; one report window answers null
    api.set_refreshed_at_for_window(date(2026, 9, 1), None)
    assert _cli(api, fake_clock, data_dir, "daily", now=_utc(2026, 10, 5, 6)) == 0

    manifest = _manifest(data_dir, "20261005T060000Z")
    assert manifest["status"] == "published"
    skipped = manifest["skipped_windows"]
    assert skipped and all("no data yet" in s for s in skipped)
    assert any("2026-09-01_2026-09-30" in s for s in skipped)
    assert not any("2026-10-01" in s for s in skipped)

    records = _all_records(data_dir)
    assert records["usage"] and records["activity"] and records["seats"]
    assert not [r for r in records["usage"] if r.period_start < date(2026, 10, 1)]
    assert all(r.data_refreshed_at is None for r in records["activity"] + records["seats"])
    assert any(r.data_refreshed_at is not None for r in records["usage"])


def test_done_check_16_cost_row_without_usage_row_is_kept(dataset, fake_clock, fake_key_env, data_dir):
    day = "2026-10-02"
    costs = {(r["actor"]["user_id"], r["product"], r["model"]): r for r in dataset["cost"][day]}
    victim = next(row for row in dataset["usage"][day]
                  if (row["actor"]["user_id"], row["product"], row["model"]) in costs)
    key = (victim["actor"]["user_id"], victim["product"], victim["model"])
    cost_row = costs[key]
    dataset["usage"][day] = [r for r in dataset["usage"][day] if r is not victim]
    api = FakeApi(dataset, clock=fake_clock, api_key=fake_key_env)
    assert _cli(api, fake_clock, data_dir, "daily", now=_utc(2026, 10, 5, 6)) == 0

    manifest = _manifest(data_dir, "20261005T060000Z")
    assert manifest["status"] == "published"
    assert any(all(part in entry for part in (key[0], day, key[1], key[2])) for entry in manifest["cost_only"])
    assert len(manifest["cost_only"]) == 1

    published = _read_kind(data_dir, "usage", UsageRecord)

    def is_victim(r):
        return (r.actor_id, r.product, r.model) == key and r.period_start == date(2026, 10, 2)

    hits = [r for r in published if is_victim(r)]
    assert len(hits) == 1
    rec = hits[0]
    assert rec.cost_only is True
    assert (rec.tokens_input_uncached, rec.tokens_output, rec.tokens_cache_write_5m,
            rec.tokens_cache_write_1h, rec.tokens_cache_read) == (0, 0, 0, 0, 0)
    assert rec.cost == Decimal(cost_row["amount"]) / Decimal(100)
    others = [r for r in published if not is_victim(r)]
    assert others and not any(r.cost_only for r in others)


def test_done_check_17_every_record_carries_the_fixture_provider_account_id(dataset, fake_clock, fake_key_env, data_dir):
    api = FakeApi(dataset, clock=fake_clock, api_key=fake_key_env)
    assert _cli(api, fake_clock, data_dir, "daily", now=_utc(2026, 10, 5, 6)) == 0
    records = _all_records(data_dir)
    for kind in ("usage", "activity", "seats", "org_totals"):
        assert records[kind], kind
        for rec in records[kind]:
            assert rec.provider_account_id not in ("", "None"), kind
            assert rec.provider_account_id == dataset["provider_account_id"], kind
