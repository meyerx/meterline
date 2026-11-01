"""MF-13: raw files, the state file and run manifests written before the rename (the provider account stored as
``org_id``) are still read, and give the same records.

Compatibility history: the field was renamed ``provider_account_id`` before the first public release.
"""
import gzip
import json
import shutil

from meterline_copilot.fetch import RunManifest
from meterline_copilot.testing import item, user_row
from copilot_helpers import at, backfill, bare_daily, latest, manifests, run

DAY = "2026-09-18"


def _api(make_api, level):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level)]
    api.billing_day[DAY] = [item()]
    return api


def _signature(data_dir, kind):
    out = {}
    for key, rec in latest(data_dir, kind).items():
        d = rec.to_dict()
        for name in ("pulled_at", "data_refreshed_at", "batch_id"):
            d.pop(name, None)
        out[key] = d
    return out


def _old_raw(data_dir):
    n = 0
    for path in sorted((data_dir / "raw").rglob("*.json.gz")):
        raw = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        raw["org_id"] = raw.pop("provider_account_id")
        path.write_bytes(gzip.compress(json.dumps(raw).encode("utf-8"), mtime=0))
        n += 1
    assert n


def _old_json(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    data["org_id"] = data.pop("provider_account_id")
    path.write_text(json.dumps(data), encoding="utf-8")


def test_raw_written_before_the_rename_rebuilds_copilot(make_api, fake_clock, data_dir, level, tmp_path):
    assert backfill(data_dir, _api(make_api, level), fake_clock, since=DAY) == 0
    expected = {kind: _signature(data_dir, kind) for kind in ("usage", "seats", "seat_assignments")}
    assert expected["usage"]
    old = tmp_path / "written-before"
    shutil.copytree(data_dir / "raw", old / "raw")
    shutil.copytree(data_dir / "runs", old / "runs")  # rebuild walks the run manifests, which carried the old name too
    _old_raw(old)
    for path in (old / "runs").glob("*.json"):
        _old_json(path)
    assert run(["rebuild", "--data-dir", str(old)], _api(make_api, level), fake_clock) == 0
    for kind, want in expected.items():
        assert _signature(old, kind) == want
        assert all("org_id" not in sig for sig in want.values())


def test_state_written_before_the_rename_resumes_copilot(make_api, fake_clock, data_dir, level):
    api = _api(make_api, level)
    assert backfill(data_dir, api, fake_clock, since=DAY) == 0
    _old_json(data_dir / "state.json")
    assert "org_id" in json.loads((data_dir / "state.json").read_text(encoding="utf-8"))
    assert bare_daily(data_dir, api, fake_clock, at("2026-09-20T07:00:00")) == 0
    state = json.loads((data_dir / "state.json").read_text(encoding="utf-8"))
    assert state["provider_account_id"] and "org_id" not in state


def test_manifest_written_before_the_rename_is_read_copilot(make_api, fake_clock, data_dir, level):
    assert backfill(data_dir, _api(make_api, level), fake_clock, since=DAY) == 0
    path = sorted((data_dir / "runs").glob("*.json"))[-1]
    new = RunManifest.read(path)
    _old_json(path)
    assert RunManifest.read(path) == new
    assert new.provider_account_id and manifests(data_dir)[-1]["org_id"] == new.provider_account_id
