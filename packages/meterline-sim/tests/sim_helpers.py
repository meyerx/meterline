"""Shared helpers of the meterline-sim done-checks (test code only)."""

import copy
import csv
import hashlib
import json
import shutil
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import meterline

# Phase 1 ran every check with Copilot disabled (spec section 14). Phase 2 flipped this
# switch, and the same checks now run with the defaults, Copilot enabled.
COPILOT_ENABLED = True
BASE_PARAMS = {"providers": {"github_copilot": {"enabled": COPILOT_ENABLED}}}
COPILOT_OFF = {"providers": {"github_copilot": {"enabled": False}}}

PRESET_DIR = Path(__file__).parent / "presets"
SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "meterline_sim"


def merged(*layers):
    """Deep merge of dicts (objects merge, everything else is replaced)."""
    out = {}
    for layer in layers:
        for key, value in (layer or {}).items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = merged(out[key], value)
            else:
                out[key] = copy.deepcopy(value)
    return out


def gen(out, preset="tiny", seed=7, months=6, params=None, **kwargs):
    import meterline_sim

    return meterline_sim.generate(
        preset, seed, months=months, out=str(out), params=merged(BASE_PARAMS, params), **kwargs
    )


def read_json(folder, name):
    return json.loads((Path(folder) / name).read_text(encoding="utf-8"))


def read_csv(folder, name):
    with open(Path(folder) / name, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(folder, name, rows, header):
    with open(Path(folder) / name, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def all_files(folder):
    folder = Path(folder)
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())


def folder_diff(a, b):
    """Human-readable differences between two output folders (empty = identical)."""
    fa, fb = all_files(a), all_files(b)
    out = []
    if fa != fb:
        out.append("file lists differ: %s" % sorted(set(fa) ^ set(fb)))
    for name in sorted(set(fa) & set(fb)):
        if (Path(a) / name).read_bytes() != (Path(b) / name).read_bytes():
            out.append("bytes differ: " + name)
    return out


def jsonl_files(folder):
    """[(kind, path)] of every batch file, sorted."""
    return [(p.parent.name, p) for p in sorted(Path(folder).glob("records/*/*.jsonl"))]


def load_records(folder, kind=None):
    """[(batch_id, record)] of every batch file of a kind (or of all kinds)."""
    out = []
    for k, path in jsonl_files(folder):
        if kind is None or k == kind:
            for rec in meterline.read_jsonl(path, meterline.KINDS[k]):
                out.append((path.stem, rec))
    return out


def by_key(folder, kind):
    """{(batch_id, record key): record dict} - one entry per record per batch."""
    return {(b, r.key()): r.to_dict() for b, r in load_records(folder, kind)}


def latest_by_key(folder, kind):
    return {k: r.to_dict() for k, r in meterline.latest_per_key(r for _b, r in load_records(folder, kind)).items()}


def pulls(folder):
    return read_json(folder, "ground_truth.json")["pulls"]


def main_batch_id(folder, provider):
    for pull in pulls(folder):
        if pull["provider"] == provider and pull["purpose"] == "main":
            return pull["batch_id"]
    return None


def mutate_jsonl(path, fn):
    lines = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    lines = fn(lines)
    Path(path).write_text(
        "".join(json.dumps(x, sort_keys=True, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8"
    )


def mutate_json(path, fn):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    fn(obj)
    Path(path).write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def mutate_csv(path, fn):
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header, rows = reader.fieldnames, list(reader)
    rows = fn(rows)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def copy_run(folder, dest):
    target = Path(dest) / "copy"
    shutil.copytree(folder, target)
    return target


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cents(text):
    return None if text is None else Decimal(text).quantize(Decimal("0.01"), ROUND_HALF_EVEN)


def parse_dt(text):
    return datetime.fromisoformat(text)


def d(text):
    return date.fromisoformat(text)


def anthropic_actor_id(folder, person_id):
    for row in read_csv(folder, "org/identities.csv"):
        if row["person_id"] == person_id and row["provider"] == "anthropic":
            return row["actor_id"]
    return None


def copilot_actor_id(folder, person_id):
    for row in read_csv(folder, "org/identities.csv"):
        if row["person_id"] == person_id and row["provider"] == "github_copilot":
            return row["actor_id"]
    return None


def copilot_person_ids(folder):
    """{actor_id: person_id} of the Copilot identities."""
    return {row["actor_id"]: row["person_id"] for row in read_csv(folder, "org/identities.csv")
            if row["provider"] == "github_copilot"}


def pull_batch_ids(folder, provider, purpose):
    return sorted(p["batch_id"] for p in pulls(folder) if p["provider"] == provider and p["purpose"] == purpose)


def revised_keys(folder, kind):
    """Keys of ``kind`` that the Copilot correction batch revises. A main-batch record under such a key is
    not the latest one, so a negative control that tampers with it would not show in the latest view."""
    ids = pull_batch_ids(folder, "github_copilot", "correction")
    path = Path(folder) / "records" / kind / ((ids[0] if ids else "none") + ".jsonl")
    if not path.is_file():
        return set()
    fields = meterline.KINDS[kind].KEY_FIELDS
    return {tuple(json.loads(x)[f] for f in fields) for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip()}


def credits_of(record):
    """The ai_credit quantity of a record dict as a Decimal (0 when it has none)."""
    return Decimal(record["quantities"].get("ai_credit", "0"))


# ---- totals of 11.3 from silver only (S05, S06) ----------------------------------------------------

TOTAL_FIELDS = ("cost", "cost_list", "tokens_input_uncached", "tokens_cache_read", "tokens_cache_write",
                "tokens_input_total", "tokens_output", "requests")


def group_totals(records_by_kind):
    """The sums of 11.3 over given records ({kind: [record dict]}): groups by (kind, provider,
    cost_basis, grain, month); a field is the sum of its non-null values, or None when all are null."""
    groups = {}
    for kind in ("usage", "org_totals"):
        for rec in records_by_kind.get(kind, ()):
            key = (kind, rec["provider"], rec["cost_basis"], rec["grain"], rec["period_start"][:7])
            groups.setdefault(key, []).append(rec)
    out = {}
    for key, recs in groups.items():
        row = {}
        for f in TOTAL_FIELDS:
            vals = [Decimal(r[f]) for r in recs if r[f] is not None]
            row[f] = sum(vals, Decimal(0)) if vals else None
        quantities = {}
        for r in recs:
            for unit, value in r["quantities"].items():
                quantities[unit] = quantities.get(unit, Decimal(0)) + Decimal(value)
        row["quantities"] = quantities
        out[key] = row
    return out


def silver_totals(folder):
    """The sums of 11.3 over the latest record per key, from silver only."""
    return group_totals({kind: list(latest_by_key(folder, kind).values()) for kind in ("usage", "org_totals")})


def compare_totals(silver, truth_rows):
    failures = []
    truth = {(t["kind"], t["provider"], t["cost_basis"], t["grain"], t["month"]): t for t in truth_rows}
    if set(silver) != set(truth):
        failures.append("groups differ: %s" % sorted(set(silver) ^ set(truth), key=repr))
    for key in sorted(set(silver) & set(truth), key=repr):
        for f, val in silver[key].items():
            want = truth[key][f]
            if f in ("cost", "cost_list"):
                same = (cents(want) == (None if val is None else val.quantize(Decimal("0.01"), "ROUND_HALF_EVEN")))
            elif f == "quantities":
                same = {u: Decimal(v) for u, v in want.items()} == val
            else:
                same = (want is None and val is None) or (want is not None and val is not None and Decimal(want) == val)
            if not same:
                failures.append("%r %s: silver %r truth %r" % (key, f, val, want))
    return failures
