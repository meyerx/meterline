"""MF-13: a raw file written before the rename (the provider account stored as ``org_id``) still rebuilds.

Compatibility history: the field was renamed ``provider_account_id`` before the first public release. The raw files
already on disk carry the old key in their envelope; ``rebuild`` reads either, and the records it writes are the same.
"""
import gzip
import json
import shutil

from admin_helpers import D1, ORG, backfill, basic_cost, basic_usage, latest, run


def _rewrite_raw_with_the_old_name(data_dir):
    changed = 0
    for path in sorted((data_dir / "raw").rglob("*.json.gz")):
        raw = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        assert raw.pop("provider_account_id") == ORG
        raw["org_id"] = ORG
        path.write_bytes(gzip.compress(json.dumps(raw).encode("utf-8"), mtime=0))
        changed += 1
    assert changed
    return changed


def _signature(data_dir, kind):
    out = {}
    for key, rec in latest(data_dir, kind).items():
        d = rec.to_dict()
        for name in ("pulled_at", "data_refreshed_at", "batch_id"):
            d.pop(name, None)
        out[key] = d
    return out


def test_raw_written_before_the_rename_rebuilds_anthropic_admin(make_api, fake_clock, data_dir, tmp_path):
    assert backfill(data_dir, make_api(basic_usage(), basic_cost()), fake_clock, since=D1) == 0
    expected = {kind: _signature(data_dir, kind) for kind in ("usage", "org_totals")}
    assert expected["usage"] and expected["org_totals"]
    old = tmp_path / "written-before"
    shutil.copytree(data_dir / "raw", old / "raw")
    shutil.copytree(data_dir / "runs", old / "runs")  # rebuild walks the run manifests, which hold no account name
    _rewrite_raw_with_the_old_name(old)
    assert run(["rebuild", "--data-dir", str(old)], make_api(basic_usage(), basic_cost()), fake_clock) == 0
    for kind, want in expected.items():
        assert _signature(old, kind) == want
        assert all(sig["provider_account_id"] == ORG and "org_id" not in sig for sig in want.values())
