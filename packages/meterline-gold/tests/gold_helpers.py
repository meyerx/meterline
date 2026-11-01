"""Helpers shared by the meterline-gold done-checks. Standard library only; nothing here imports the package."""

import csv
import hashlib
import hmac
import io
import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "docs" / "gold-fixture"
EXPECTED = FIXTURE / "expected"
KEY = b"meterline-gold-fixture-key-not-a-secret"


def fixture_inputs():
    return {
        "silver": FIXTURE / "input" / "silver",
        "org": FIXTURE / "input" / "org",
        "policy": FIXTURE / "input" / "policy.yaml",
        "prices": FIXTURE / "input" / "prices.yaml",
        "invoice": FIXTURE / "input" / "invoice.csv",
    }


def H(text, key=KEY):
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def person_key(person_id, key=KEY):
    return "pk_" + H("person|" + person_id, key)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_csv(path):
    """(header, rows) with every field a string; an empty field is ''."""
    with open(path, "r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    return rows[0], rows[1:]


def read_dicts(path):
    header, rows = read_csv(path)
    return [dict(zip(header, row)) for row in rows]


def folder_bytes(folder):
    """Relative POSIX path -> bytes of every file under folder, Parquet files left out."""
    folder = Path(folder)
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.suffix != ".parquet"
    }


def assert_bytes_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError("%s: bytes differ (%d vs %d bytes)" % (label, len(actual), len(expected)))


def one_value_changed(data):
    """The same bytes with the last digit of the last data line replaced by another digit."""
    start = data.index(b"\n") + 1
    for i in range(len(data) - 1, start - 1, -1):
        if 48 <= data[i] <= 57:
            changed = bytes([48 + (data[i] - 48 + 1) % 10])
            return data[:i] + changed + data[i + 1:]
    raise AssertionError("no digit to change")


def compare_with_control(actual_path, expected_path, label):
    """Byte comparison plus its negative control (spec 14)."""
    actual = Path(actual_path).read_bytes()
    expected = Path(expected_path).read_bytes()
    assert_bytes_equal(actual, expected, label)
    try:
        assert_bytes_equal(actual, one_value_changed(expected), label + " (negative control)")
    except AssertionError:
        return
    raise AssertionError(label + ": the negative control did not fail")


def threads1_cli_script():
    return (
        "import sys, meterline_gold\n"
        "from meterline_gold import cli\n"
        "real = meterline_gold.build\n"
        "def build_threads1(*a, **k):\n"
        "    k['threads'] = 1\n"
        "    return real(*a, **k)\n"
        "meterline_gold.build = build_threads1\n"
        "if hasattr(cli, 'build'):\n"
        "    cli.build = build_threads1\n"
        "sys.exit(cli.main(sys.argv[1:]))\n"
    )


def run_cli_subprocess(args, key=KEY, hashseed="4242", threads1=True):
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hashseed
    env["METERLINE_GOLD_KEY"] = key.decode("utf-8")
    if threads1:
        cmd = [sys.executable, "-c", threads1_cli_script()] + [str(a) for a in args]
    else:
        cmd = [sys.executable, "-c", "import sys; from meterline_gold import cli; sys.exit(cli.main(sys.argv[1:]))"] + [
            str(a) for a in args
        ]
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


# --- silver reading, independent of the package under test -------------------------------------


def current_silver(silver, kind):
    from meterline import KINDS, latest_per_key, read_jsonl

    records = []
    for path in sorted((Path(silver) / "records" / kind).glob("*.jsonl")):
        records.extend(read_jsonl(path, KINDS[kind]))
    return list(latest_per_key(records).values())


MEASURES = (
    "requests", "tokens_input_uncached", "tokens_cache_read", "tokens_cache_write",
    "tokens_cache_write_5m", "tokens_cache_write_1h", "tokens_input_total", "tokens_output",
)


def silver_usage_groups(silver):
    """(provider, cost_basis or '', month) -> measure -> Decimal, over current day-grain usage records
    whose actor kind is not end_user."""
    groups = {}
    for rec in current_silver(silver, "usage"):
        if rec.grain != "day" or rec.actor_kind == "end_user":
            continue
        key = (rec.provider, rec.cost_basis or "", rec.period_start.isoformat()[:7])
        g = groups.setdefault(key, {m: Decimal(0) for m in MEASURES + ("cost", "ai_credit")})
        for m in MEASURES:
            g[m] += Decimal(getattr(rec, m) or 0)
        g["cost"] += rec.cost if rec.cost is not None else Decimal(0)
        g["ai_credit"] += rec.quantities.get("ai_credit", Decimal(0))
    return groups


def person_day_groups(person_day_csv):
    groups = {}
    for row in read_dicts(person_day_csv):
        key = (row["provider"], row["cost_source_basis"], row["month"])
        g = groups.setdefault(key, {m: Decimal(0) for m in MEASURES + ("cost", "ai_credit")})
        for m in MEASURES:
            g[m] += Decimal(row[m] or 0)
        g["cost"] += Decimal(row["cost_source"] or 0)
        g["ai_credit"] += Decimal(row["credits"] or 0)
    return groups


def filter_silver(src, dst, last_day):
    """Copy a silver folder keeping only the lines whose period_start is on or before last_day (ISO text)."""
    src, dst = Path(src), Path(dst)
    for path in sorted(src.rglob("*.jsonl")):
        target = dst / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        kept = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line)["period_start"] <= last_day:
                kept.append(line)
        target.write_text("".join(l + "\n" for l in kept), encoding="utf-8")
    return dst


# --- scans --------------------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def scan_identifying(folder, directory_csv):
    """Findings (path, token) of an email address anywhere, or a given/family name of the directory as a
    whole word (case-sensitive), in every file other than delivery/directory.csv."""
    folder = Path(folder)
    names = set()
    for row in read_dicts(directory_csv):
        names.add(row["given_name"])
        names.add(row["family_name"])
    names.discard("")
    name_re = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(n) for n in sorted(names))) if names else None
    findings = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix == ".parquet":
            continue
        rel = path.relative_to(folder).as_posix()
        if rel == "delivery/directory.csv":
            continue
        text = path.read_text(encoding="utf-8")
        for m in EMAIL_RE.finditer(text):
            findings.append((rel, m.group(0)))
        if name_re is not None:
            for m in name_re.finditer(text):
                findings.append((rel, m.group(0)))
    return findings


FORBIDDEN_NAMES = ("datetime.now", "date.today", "time.time", "uuid", "random", "os.urandom")


def scan_forbidden_names(root):
    findings = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or path.suffix in (".pyc", ".parquet"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name in FORBIDDEN_NAMES:
            if name in text:
                findings.append((path.name, name))
    return findings


def strip_sql(text):
    """The SQL with comments and single-quoted string literals removed."""
    out = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif text[i] == "'":
            i += 1
            while i < n:
                if text[i] == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def split_statements(text):
    """Raw statements (comments kept) split on ';' outside strings and comments."""
    stmts, buf = [], []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            buf.append(text[i:j])
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            buf.append(text[i:j])
            i = j
        elif text[i] == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            buf.append(text[i:j])
            i = j
        elif text[i] == ";":
            stmts.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(text[i])
            i += 1
    if "".join(buf).strip():
        stmts.append("".join(buf))
    return stmts


DATASET_MARK_RE = re.compile(r"^\s*--\s*dataset:\s*(\S+)\s*$", re.M)


def dataset_queries(sql_dir):
    """dataset name -> list of statements marked with a '-- dataset: <name>' comment line."""
    found = {}
    for path in sorted(Path(sql_dir).glob("*.sql")):
        for stmt in split_statements(path.read_text(encoding="utf-8")):
            for m in DATASET_MARK_RE.finditer(stmt):
                found.setdefault(m.group(1), []).append(stmt)
    return found


def ends_with_order_by(statement):
    body = strip_sql(statement).strip()
    return re.search(r"\border\s+by\b[^()]*$", body, re.I | re.S) is not None


def sql_scan_findings(sql_dir):
    """Findings of the G20 SQL scans over a folder of .sql files."""
    findings = []
    for path in sorted(Path(sql_dir).glob("*.sql")):
        raw = path.read_text(encoding="utf-8")
        if path.name != "00_macros.sql" and "/" in strip_sql(raw):
            findings.append((path.name, "slash"))
        for pattern in (r"\bdouble\b", r"\bfloat\b", r"\bavg\s*\(", r"quantile_cont", r"\bmedian\s*\("):
            if re.search(pattern, raw, re.I):
                findings.append((path.name, pattern))
    return findings


def forbidden_imports(source, names=("urllib", "http", "socket", "requests")):
    import ast

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        else:
            continue
        for mod in mods:
            if mod.split(".")[0] in names:
                found.append(mod)
    return found


# --- manifest -----------------------------------------------------------------------------------


def manifest_problems(folder):
    """Differences between manifest.json and the files of a gold folder (outputs, bytes, sha256, rows)."""
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    files = {
        p.relative_to(folder).as_posix(): p
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.name != "manifest.json"
    }
    listed = {o["path"]: o for o in manifest["outputs"]}
    if sorted(listed) != sorted(files):
        problems.append("outputs differ from the files: %s" % sorted(set(listed) ^ set(files)))
    if [o["path"] for o in manifest["outputs"]] != sorted(listed):
        problems.append("outputs not sorted")
    for rel, path in files.items():
        entry = listed.get(rel)
        if entry is None:
            continue
        data = path.read_bytes()
        if entry["bytes"] != len(data):
            problems.append(rel + ": bytes")
        if entry["sha256"] != sha256_bytes(data):
            problems.append(rel + ": sha256")
        if rel.endswith(".csv"):
            rows = list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))
            if entry["rows"] != len(rows) - 1:
                problems.append(rel + ": rows")
    return problems


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# --- example policy: the 4.4 money rule -----------------------------------------------------------


def policy_money_amounts(policy):
    """Every money amount of a parsed policy that the GD1 rule covers (not band thresholds)."""
    amounts = []
    for budget in (policy.get("pools") or {}).get("budgets") or []:
        amounts.append(budget["monthly_amount"])
    alerts = policy.get("alerts") or {}
    if alerts.get("daily_breaker") is not None:
        amounts.append(alerts["daily_breaker"])
    if alerts.get("spike"):
        amounts.append(alerts["spike"]["floor"])
    for rule in policy.get("named_exceptions") or []:
        if rule["condition"] == "month_cost_ref_above":
            amounts.append(rule["threshold"])
    for scenario in policy.get("scenarios") or []:
        caps = scenario.get("person_caps")
        if caps:
            if caps.get("default") is not None:
                amounts.append(caps["default"])
            amounts.extend((caps.get("values") or {}).values())
        envelopes = scenario.get("unit_envelopes")
        if envelopes:
            amounts.extend((envelopes.get("values") or {}).values())
        if scenario.get("top_up_amount") is not None:
            amounts.append(scenario["top_up_amount"])
    return amounts


def money_rule_violations(amounts):
    bad = []
    for text in amounts:
        value = Decimal(str(text))
        exp = value.as_tuple().exponent
        if exp != -2 or str(text).endswith("0"):
            bad.append(text)
    return bad


# --- phase 3 helpers: building from a modified copy of the fixture input ---------------------------


def copy_fixture_input(dst):
    """Copy docs/gold-fixture/input into dst; returns the same dict as fixture_inputs() for the copy."""
    import shutil

    dst = Path(dst)
    shutil.copytree(FIXTURE / "input", dst)
    return {
        "silver": dst / "silver", "org": dst / "org", "policy": dst / "policy.yaml",
        "prices": dst / "prices.yaml", "invoice": dst / "invoice.csv",
    }


def edit_csv(path, edit):
    """Read a CSV file, let edit(header, rows) change the list of row lists, write it back (LF, minimal quoting)."""
    header, rows = read_csv(path)
    edit(header, rows)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def edit_policy(path, edit):
    import yaml

    obj = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    edit(obj)
    Path(path).write_text(yaml.safe_dump(obj, sort_keys=True, default_flow_style=False), encoding="utf-8")


def build_cli(inp, out, monkeypatch=None, key=KEY):
    """Run ``meterline-gold build`` through cli.main on a dict of inputs; returns the exit code."""
    from meterline_gold import cli

    if monkeypatch is not None:
        monkeypatch.setenv("METERLINE_GOLD_KEY", key.decode("utf-8"))
    args = ["build", "--silver", str(inp["silver"]), "--org-files", str(inp["org"]), "--policy", str(inp["policy"]),
            "--prices", str(inp["prices"]), "--out", str(out)]
    if inp.get("invoice") is not None:
        args += ["--invoice", str(inp["invoice"])]
    return cli.main(args)


def tiny_key_dirs(sim_run):
    return {"silver": sim_run.silver, "org": sim_run.org}


# --- stronger controls: a fixture variant in which every provider has at least k users ------------------------


def _append_jsonl(path, records):
    with open(path, "a", encoding="utf-8", newline="") as handle:
        for rec in records:
            handle.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def all_providers_at_k_inputs(dst, copilot=True, litellm=True):
    """A copy of the fixture input in which every provider has at least k = 5 resolved users in 2026-05.

    The fixture has 13 Anthropic users, 4 GitHub Copilot users and 1 LiteLLM user, so exec hides the last two. The
    copy adds one Copilot user (p20, whose identity row is already there) and four LiteLLM users (p07-p10), each with
    one day record and a matching organisation-total record, so that the reconciliation stays exact. Nothing else is
    changed. ``copilot=False`` or ``litellm=False`` leaves that provider as it is in the fixture. Returns the same
    dict as ``copy_fixture_input``.
    """
    inp = copy_fixture_input(dst)
    records = Path(inp["silver"]) / "records"
    usage_copilot = records / "usage" / "20260603T061000Z.jsonl"
    totals_copilot = records / "org_totals" / "20260603T061000Z.jsonl"
    usage_litellm = records / "usage" / "20260610T062000Z.jsonl"
    totals_litellm = records / "org_totals" / "20260610T062000Z.jsonl"

    if copilot:
        _add_copilot_user(usage_copilot, totals_copilot)
    if litellm:
        _add_litellm_users(inp, usage_litellm, totals_litellm)
    return inp


def _add_copilot_user(usage_copilot, totals_copilot):
    template = next(r for r in _read_jsonl(usage_copilot) if r["grain"] == "day")
    new = dict(template, actor_id="200020", actor_login="reto-zamabe", period_start="2026-05-27",
               period_end="2026-05-27", cost="0.30", cost_list="0.30", quantities={"ai_credit": "30"})
    total = next(r for r in _read_jsonl(totals_copilot) if r["grain"] == "day")
    new_total = dict(total, period_start="2026-05-27", period_end="2026-05-27", cost="0.00", cost_list="0.30",
                     quantities={"ai_credit": "30"},
                     extras=dict(total["extras"], discount_quantity="30"))
    _append_jsonl(usage_copilot, [new])
    _append_jsonl(totals_copilot, [new_total])


def _add_litellm_users(inp, usage_litellm, totals_litellm):
    template = next(r for r in _read_jsonl(usage_litellm) if r["actor_id"] == "lu-06")
    total = next(r for r in _read_jsonl(totals_litellm) if r["period_start"] == "2026-05-13")
    users, totals, identities = [], [], []
    for n in range(7, 11):
        day = "2026-05-%02d" % (20 + n)
        users.append(dict(template, actor_id="lu-%02d" % n, actor_email="x-p%02d@example.com" % n,
                          period_start=day, period_end=day, cost="1.00"))
        totals.append(dict(total, period_start=day, period_end=day, cost="1.00"))
        identities.append(["p%02d" % n, "litellm", "oidc_claim", "proxy_user", "lu-%02d" % n, "",
                           "x-p%02d@example.com" % n, "2026-05-01", ""])
    _append_jsonl(usage_litellm, users)
    _append_jsonl(totals_litellm, totals)
    edit_csv(Path(inp["org"]) / "identities.csv", lambda header, rows: rows.extend(identities))
