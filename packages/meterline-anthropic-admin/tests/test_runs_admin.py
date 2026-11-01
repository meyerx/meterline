"""The commands end to end on the fake: backfill, daily twice, rebuild to the same bytes, the workspace filter, the
state file, raw files that are never replaced, private permissions and the public interface."""

import hashlib
import importlib
import importlib.metadata
import os
import re
import stat
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from admin_helpers import D1, D2, NOW, ORG, backfill, basic_cost, basic_usage, daily, latest, manifests, records, run, totals_by_key
from admin_helpers import BASE_URL, FAKE_KEY
from meterline_anthropic_admin.testing import COST_PATH, USAGE_PATH, crow, urow, utc
from meterline_anthropic_admin.client import AdminClient
from meterline_anthropic_admin.fetch import RawFileExists, fetch_endpoint

PACKAGE_DIR = Path(__file__).resolve().parents[1]


def _tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(root).rglob("*")) if p.is_file()}


def test_backfill_writes_raw_runs_records_and_state(make_api, fake_clock, data_dir, capsys):
    assert backfill(data_dir, make_api(basic_usage(), basic_cost()), fake_clock) == 0
    assert "ok: backfill published" in capsys.readouterr().out
    raw = sorted(p.relative_to(data_dir).as_posix() for p in (data_dir / "raw").rglob("*.json.gz"))
    assert len(raw) == 2 and raw[0].startswith("raw/cost_report/") and raw[1].startswith("raw/usage_report_messages/")
    assert re.fullmatch(r"raw/cost_report/2026-09-14_2026-09-19/20260920T060000Z\.json\.gz", raw[0])
    manifest = manifests(data_dir)[0]
    assert manifest["status"] == "published" and manifest["published_counts"] == {"usage": 5, "org_totals": 4}
    assert manifest["totals"]["2026-09-15.model-a.tokens_output"] == "95"
    assert len(records(data_dir, "usage")) == 5 and len(records(data_dir, "org_totals")) == 4
    state = (data_dir / "state.json").read_text(encoding="utf-8")
    # 09-14 has no rows and is final (48 hours past its end); 09-18 and 09-19 have none and are still open, so they stay
    # unloaded and the next daily reads them again (MF-3)
    assert '"2026-09-14"' in state and '"2026-09-17"' in state and '"pending": null' in state
    assert '"2026-09-18"' not in state and '"2026-09-19"' not in state


def test_daily_twice_publishes_nothing_the_second_time(make_api, fake_clock, data_dir):
    api = make_api(basic_usage(), basic_cost())
    assert daily(data_dir, api, fake_clock, NOW, "--since", "2026-09-14") == 0
    before = sorted(p.name for p in (data_dir / "records" / "usage").glob("*.jsonl"))
    assert daily(data_dir, api, fake_clock, utc("2026-09-20T07:00:00")) == 0
    assert sorted(p.name for p in (data_dir / "records" / "usage").glob("*.jsonl")) == before
    assert manifests(data_dir)[-1]["published_counts"] == {"usage": 0, "org_totals": 0}


def test_daily_without_a_state_file_asks_for_since(make_api, fake_clock, data_dir, capsys):
    assert daily(data_dir, make_api(basic_usage()), fake_clock) == 2
    assert "--since" in capsys.readouterr().err


def test_daily_picks_up_a_changed_value_in_a_day_that_is_not_final(make_api, fake_clock, data_dir):
    api = make_api(basic_usage() + [urow("2026-09-19", "apikey_a", "model-a", uncached=80)], basic_cost())
    assert daily(data_dir, api, fake_clock, NOW, "--since", "2026-09-14") == 0
    api.usage_rows[-1]["uncached"] = 95
    assert daily(data_dir, api, fake_clock, utc("2026-09-20T08:00:00")) == 0
    new = latest(data_dir, "usage")
    key = next(k for k, r in new.items() if r.period_start.isoformat() == "2026-09-19")
    assert new[key].tokens_input_uncached == 95
    assert len(records(data_dir, "usage")) == 7  # six, and the revised one: the earlier batch stays readable


def test_rebuild_gives_the_same_bytes_without_the_network(make_api, fake_clock, data_dir):
    api = make_api(basic_usage() + [urow("2026-09-19", "apikey_a", "model-a", uncached=80)], basic_cost())
    assert backfill(data_dir, api, fake_clock) == 0
    api.usage_rows[-1]["uncached"] = 1234  # a day that is not final yet, so the next daily reads it again
    assert daily(data_dir, api, fake_clock, utc("2026-09-20T09:00:00")) == 0
    before = _tree_hashes(data_dir / "records")
    assert len(before) == 4  # two batches, two kinds
    requests = len(api.log)
    assert run(["rebuild", "--data-dir", str(data_dir)], api, fake_clock) == 0
    assert _tree_hashes(data_dir / "records") == before
    assert len(api.log) == requests


def test_rebuild_with_a_raw_file_missing_exits_2_and_leaves_records(make_api, fake_clock, data_dir, capsys):
    api = make_api(basic_usage(), basic_cost())
    assert backfill(data_dir, api, fake_clock) == 0
    before = _tree_hashes(data_dir / "records")
    next((data_dir / "raw" / "cost_report").rglob("*.json.gz")).unlink()
    assert run(["rebuild", "--data-dir", str(data_dir)], api, fake_clock) == 2
    assert "missing" in capsys.readouterr().err
    assert _tree_hashes(data_dir / "records") == before


def test_workspace_filter_is_sent_and_applied_to_the_cost_rows_too(make_api, fake_clock, data_dir):
    usage = basic_usage() + [urow(D1, "apikey_c", "model-a", ws="wrkspc_b", uncached=7)]
    cost = basic_cost() + [crow(D1, "model-a", "output_tokens", "500", ws="wrkspc_b")]
    api = make_api(usage, cost)
    assert backfill(data_dir, api, fake_clock, "2026-09-14", NOW, "--workspace-id", "wrkspc_a") == 0
    assert api.requests_for(USAGE_PATH)[0].params["workspace_ids[]"] == ["wrkspc_a"]
    assert {r.actor_id for r in records(data_dir, "usage")} == {"apikey_a", "apikey_b"}  # no console row, no other workspace
    assert totals_by_key(data_dir)[("api", D1, "model-a")].cost == Decimal("1.3428912")  # the other workspace's 5 USD is not in it
    assert all(e.params.get("workspace_ids[]") == ["wrkspc_a"] for e in api.requests_for(USAGE_PATH))


def test_provider_account_id_is_required_and_never_empty(make_api, fake_clock, data_dir, capsys):
    with pytest.raises(SystemExit):
        run(["backfill", "--since", "2026-09-14", "--data-dir", str(data_dir)], make_api(basic_usage()), fake_clock)
    assert run(["backfill", "--since", "2026-09-14", "--data-dir", str(data_dir), "--provider-account-id", "  "], make_api(basic_usage()), fake_clock) == 2
    assert "--provider-account-id" in capsys.readouterr().err


def test_a_damaged_state_file_stops_the_run_before_any_request(make_api, fake_clock, data_dir, capsys):
    (data_dir / "state.json").write_text("{not json", encoding="utf-8")
    api = make_api(basic_usage(), basic_cost())
    assert backfill(data_dir, api, fake_clock) == 2
    assert api.log == [] and "state.json" in capsys.readouterr().err
    assert daily(data_dir, api, fake_clock) == 2 and api.log == []


def test_a_raw_file_is_never_replaced(make_api, fake_clock, data_dir):
    api = make_api(basic_usage(), basic_cost())
    kw = dict(first=date(2026, 9, 14), last=date(2026, 9, 19), pulled_at=NOW, provider_account_id=ORG, workspace_id=None)
    with AdminClient(FAKE_KEY, base_url=BASE_URL, transport=api.transport, clock=fake_clock, sleep=fake_clock.sleep) as client:
        first = fetch_endpoint(client, data_dir, "usage_report_messages", **kw)
        before = first.path.read_bytes()
        api.usage_rows.pop()
        with pytest.raises(RawFileExists, match="never replaced"):
            fetch_endpoint(client, data_dir, "usage_report_messages", **kw)
    assert first.path.read_bytes() == before and not list(data_dir.rglob("*.tmp"))
    assert backfill(data_dir, api, fake_clock, "2026-09-14", NOW) == 2  # a pull in the same second as a finished one is refused


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_output_is_private_whatever_the_umask(make_api, fake_clock, tmp_path):
    data_dir = tmp_path / "new-parent" / "data"
    api = make_api(basic_usage(), basic_cost())
    old = os.umask(0o022)
    try:
        assert backfill(data_dir, api, fake_clock) == 0
        assert run(["rebuild", "--data-dir", str(data_dir)], api, fake_clock) == 0
        seen = os.umask(0)
        os.umask(seen)
        assert seen == 0o022, "the run left the process umask changed"
    finally:
        os.umask(old)
    paths = [data_dir.parent, *data_dir.parent.rglob("*")]
    assert {p.as_posix(): oct(stat.S_IMODE(p.stat().st_mode)) for p in paths if p.is_file() and stat.S_IMODE(p.stat().st_mode) != 0o600} == {}
    assert {p.as_posix(): oct(stat.S_IMODE(p.stat().st_mode)) for p in paths if p.is_dir() and stat.S_IMODE(p.stat().st_mode) != 0o700} == {}


def test_the_default_data_directory_is_exports_meterline_anthropic_admin():
    from meterline_anthropic_admin import cli

    assert cli.DEFAULT_DATA_DIR == "exports/meterline-anthropic-admin"
    assert cli.KEY_ENV == "ANTHROPIC_ADMIN_KEY"


def test_version_matches_metadata_and_is_not_a_literal():
    module = importlib.import_module("meterline_anthropic_admin")
    text = (PACKAGE_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert module.__version__ == importlib.metadata.version("meterline-anthropic-admin") == re.search(r'^version = "([^"]+)"$', text, re.M).group(1)
    assert not re.search(r"__version__\s*=\s*['\"]\d+\.", (PACKAGE_DIR / "src" / "meterline_anthropic_admin" / "__init__.py").read_text(encoding="utf-8"))
    assert "__version__" in module.__all__ and all(hasattr(module, n) for n in module.__all__)


def test_readme_names_the_public_interface_and_what_is_internal():
    text = (PACKAGE_DIR / "README.md").read_text(encoding="utf-8")
    section = text[text.index("\n## Public interface\n"):]
    assert "`meterline_anthropic_admin.__all__`" in section and "internal" in section
