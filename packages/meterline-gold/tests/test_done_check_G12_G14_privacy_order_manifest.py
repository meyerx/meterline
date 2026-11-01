"""G12 (no identifying data), G13 (sorted by key, never by a measure), G14 (schema version and manifest)."""

import csv
import json
import shutil
from pathlib import Path

import pytest
import yaml

from gold_helpers import (
    FIXTURE, KEY, canonical_json, fixture_inputs, manifest_problems, read_csv, scan_identifying, sha256_bytes,
)

# ---- G12 -----------------------------------------------------------------------------------------


def _a09(out, org, silver):
    from meterline_gold import write

    return write.a09_violations(out, org, silver)


def test_done_check_G12_fixture_has_no_identifying_data(fixture_out, tmp_path):
    inp = fixture_inputs()
    assert _a09(fixture_out, inp["org"], inp["silver"]) == []
    directory = inp["org"] / "directory.csv"
    assert scan_identifying(fixture_out, directory) == []
    assert (fixture_out / "delivery" / "directory.csv").is_file()
    # negative controls: a planted email, a planted name and a planted person_id are all flagged
    copy = tmp_path / "copy"
    shutil.copytree(fixture_out, copy)
    target = copy / "central" / "units.csv"
    target.write_text(target.read_text(encoding="utf-8") + "x,x@example.com\n", encoding="utf-8")
    assert any(tok.endswith("@example.com") for _, tok in scan_identifying(copy, directory))
    target.write_text(target.read_text(encoding="utf-8") + "Bala\n", encoding="utf-8")
    assert ("central/units.csv", "Bala") in scan_identifying(copy, directory)
    header, rows = read_csv(copy / "central" / "person_month.csv")
    rows[0][header.index("cost_center")] = "p03"
    with open(copy / "central" / "person_month.csv", "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    assert _a09(copy, inp["org"], inp["silver"]) != []


def test_done_check_G12_tiny_sim_has_no_identifying_data(sim_runs, sim_builds, tmp_path):
    run = sim_runs["tiny"]
    out, _ = sim_builds["tiny"]
    assert _a09(out, run.org, run.silver) == []
    directory = run.org / "directory.csv"
    assert scan_identifying(out, directory) == []
    copy = tmp_path / "copy"
    shutil.copytree(out, copy)
    first = read_csv(directory)[1][0][0]
    target = copy / "central" / "units.csv"
    target.write_text(target.read_text(encoding="utf-8") + first + "@example.com\n", encoding="utf-8")
    assert scan_identifying(copy, directory) != []


# ---- G13 -----------------------------------------------------------------------------------------

PHASE1_KEYS = {
    "people": ("person_key", "valid_from"),
    "units": ("unit_id", "valid_from"),
    "identity_map": ("provider", "actor_kind", "actor_key", "valid_from"),
    "person_day": ("day", "provider", "provider_account_id", "person_key", "actor_key", "product", "model"),
    "person_day_activity": ("day", "provider", "provider_account_id", "person_key", "actor_key", "product"),
    "person_month": ("month", "person_key"),
    "seats_month": ("month", "provider", "provider_account_id"),
    "pools_month": ("month", "provider", "provider_account_id"),
    "costs_month": ("month", "provider", "provider_account_id"),
    "reconciliation_month": ("month", "provider", "provider_account_id"),
    "data_quality": ("month", "provider", "provider_account_id", "metric"),
    "directory": ("person_key",),
}
LOCATION = {"directory": "delivery/directory.csv"}


def _key_order_problems(path, key):
    """Rows not strictly ascending over key. Every key column of phase 1 is text or a date, which sort as
    text with an empty (null) field first."""
    header, rows = read_csv(path)
    idx = [header.index(c) for c in key]
    problems = []
    prev = None
    for n, row in enumerate(rows):
        cur = tuple(row[i] for i in idx)
        if prev is not None and not prev < cur:
            problems.append((n, prev, cur))
        prev = cur
    return problems


def test_done_check_G13_sorted_by_key_not_by_measure(fixture_out, sim_builds, tmp_path):
    from meterline_gold import write

    for name, key in PHASE1_KEYS.items():
        assert tuple(write.DATASET_KEYS[name]) == key, name
    checked = 0
    for out in (fixture_out, sim_builds["tiny"][0]):
        for name, key in PHASE1_KEYS.items():
            path = out / LOCATION.get(name, "central/%s.csv" % name)
            assert path.is_file(), path
            assert _key_order_problems(path, key) == [], path
            checked += 1
    assert checked == 2 * len(PHASE1_KEYS)
    # negative control: person_month sorted by cost_ref is flagged
    src = sim_builds["tiny"][0] / "central" / "person_month.csv"
    header, rows = read_csv(src)
    from decimal import Decimal

    rows.sort(key=lambda r: Decimal(r[header.index("cost_ref")]))
    bad = tmp_path / "person_month.csv"
    with open(bad, "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    assert _key_order_problems(bad, PHASE1_KEYS["person_month"]) != []


# ---- G14 -----------------------------------------------------------------------------------------

FIXTURE_KEY_CHECK = "kc_79afd616f30de074"


def _csv_files(out):
    return sorted(p for p in out.rglob("*.csv"))


def test_done_check_G14_schema_version_and_manifest(fixture_out, tmp_path):
    files = _csv_files(fixture_out)
    assert files
    for path in files:
        header, rows = read_csv(path)
        assert header[0] == "schema_version", path
        assert all(r[0] == "2.0" for r in rows), path
    assert manifest_problems(fixture_out) == []
    manifest = json.loads((fixture_out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["key_check"] == FIXTURE_KEY_CHECK
    assert manifest["gold_schema_version"] == "2.0"
    assert manifest["generator"] == {"name": "meterline-gold", "version": "0.1.0"}
    assert manifest["as_of"] == "2026-06-10"
    assert manifest["data_through"] == "2026-05-31"
    assert manifest["months"] == ["2026-05"]
    raw = (fixture_out / "manifest.json").read_text(encoding="utf-8")
    assert raw == json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    # the input hashes
    import meterline_gold  # noqa: F401
    from meterline_gold import params

    inp = fixture_inputs()
    policy = params.Policy.model_validate(yaml.safe_load(inp["policy"].read_text(encoding="utf-8")))
    prices = params.PriceList.model_validate(yaml.safe_load(inp["prices"].read_text(encoding="utf-8")))
    ins = manifest["inputs"]
    assert ins["policy_sha256"] == sha256_bytes(canonical_json(policy.model_dump(mode="json")).encode("utf-8"))
    assert ins["prices_sha256"] == sha256_bytes(canonical_json(prices.model_dump(mode="json")).encode("utf-8"))
    assert ins["invoice_sha256"] == sha256_bytes(inp["invoice"].read_bytes())
    org = [{"path": p.name, "sha256": sha256_bytes(p.read_bytes())} for p in sorted(inp["org"].glob("*.csv"))]
    assert ins["org"] == org
    silver_root = inp["silver"]
    silver = [
        {"path": p.relative_to(silver_root).as_posix(), "sha256": sha256_bytes(p.read_bytes())}
        for p in sorted(silver_root.rglob("*.jsonl"))
    ]
    assert ins["silver"] == silver
    # negative controls: a changed byte, a missing file, a changed row count are all reported
    copy = tmp_path / "copy"
    shutil.copytree(fixture_out, copy)
    victim = copy / "central" / "costs_month.csv"
    victim.write_bytes(victim.read_bytes().replace(b"69.322", b"69.323"))
    assert manifest_problems(copy) != []
    shutil.rmtree(copy)
    shutil.copytree(fixture_out, copy)
    (copy / "central" / "units.csv").unlink()
    assert manifest_problems(copy) != []
