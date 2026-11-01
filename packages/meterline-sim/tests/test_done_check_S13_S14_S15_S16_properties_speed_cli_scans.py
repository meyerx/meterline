"""S13 property tests, S14 speed, S15 CLI, S16 determinism and genericity scans."""

import csv
import json
import re
import shutil
import tempfile
import time
from pathlib import Path

import pytest
pytest.importorskip("hypothesis")  # a base install lacks the dev extra: skip this module
from hypothesis import given, settings
from hypothesis import strategies as st

import meterline_sim
from meterline_sim import cli
from sim_helpers import BASE_PARAMS, SRC_DIR, folder_diff, gen, jsonl_files, load_records, pulls


# ---- S13 ---------------------------------------------------------------------


@settings(max_examples=50, deadline=None, derandomize=True)
@given(seed=st.integers(0, 2**31 - 1))
def test_done_check_S13_property_every_invariant_holds(seed):
    tmp = Path(tempfile.mkdtemp())
    try:
        folder = gen(tmp, "tiny", seed, 2)
        assert meterline_sim.verify(folder) == []
        # the property holds with Copilot in the run: its main batch and seat lists were written
        copilot = [p for p in pulls(folder) if p["provider"] == "github_copilot"]
        assert any(p["purpose"] == "main" for p in copilot) and any(p["purpose"] == "seat_list" for p in copilot)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@settings(max_examples=5, deadline=None, derandomize=True)
@given(seed=st.integers(0, 2**31 - 1))
def test_done_check_S13_property_two_runs_are_byte_identical(seed):
    tmp = Path(tempfile.mkdtemp())
    try:
        a = gen(tmp / "a", "tiny", seed, 2)
        b = gen(tmp / "b", "tiny", seed, 2)
        assert folder_diff(a, b) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_done_check_S13_negative_control_a_run_without_copilot_has_no_copilot_pulls(tmp_path):
    from sim_helpers import COPILOT_OFF

    folder = gen(tmp_path, "tiny", 5, 2, COPILOT_OFF)
    assert not [p for p in pulls(folder) if p["provider"] == "github_copilot"]  # the property run's assertion


def test_done_check_S13_negative_control_verify_reports_a_broken_folder(tmp_path):
    folder = gen(tmp_path, "tiny", 5, 2)
    assert meterline_sim.verify(folder) == []
    for kind, path in jsonl_files(folder):
        if kind == "usage":
            lines = path.read_text(encoding="utf-8").splitlines()
            first = json.loads(lines[0])
            first["requests"] = int(first["requests"]) + 1
            lines[0] = json.dumps(first, sort_keys=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            break
    assert meterline_sim.verify(folder) != []


# ---- S14 ---------------------------------------------------------------------

TIME_BOUND = 10.0


def within_bound(seconds):
    return seconds < TIME_BOUND


@pytest.mark.timing
def test_done_check_S14_speed(tmp_path):
    started = time.perf_counter()
    gen(tmp_path, "tiny", 7, 6)
    elapsed = time.perf_counter() - started
    print("S14 tiny run, 6 months: %.2f s (bound %.0f s)" % (elapsed, TIME_BOUND))
    assert within_bound(elapsed)


def test_done_check_S14_negative_control_slow_run_is_flagged():
    assert within_bound(TIME_BOUND + 1.0) is False


# ---- S15 ---------------------------------------------------------------------


def _params_file(tmp_path):
    path = tmp_path / "params.json"
    path.write_text(json.dumps(BASE_PARAMS), encoding="utf-8")
    return str(path)


def _argv(tmp_path, *extra, preset="tiny", months="2"):
    return ["generate", "--preset", preset, "--seed", "1", "--months", months,
            "--out", str(tmp_path / "out"), "--params", _params_file(tmp_path), *extra]


def test_done_check_S15_cli(tmp_path, monkeypatch):
    assert cli.main(_argv(tmp_path)) == 0
    folder = tmp_path / "out" / "tiny-s1-m2"
    assert (folder / "org").is_dir() and (folder / "records").is_dir()
    assert (folder / "ground_truth.json").is_file() and (folder / "manifest.json").is_file()

    assert cli.main(_argv(tmp_path)) == 2  # the folder is not empty
    assert cli.main(_argv(tmp_path, preset="nosuch")) == 2
    assert cli.main(_argv(tmp_path, months="0")) == 2

    from meterline_sim import render

    monkeypatch.setattr(render, "batch_failures", lambda records, today=None: ["forced"])
    other = tmp_path / "forced"
    argv = ["generate", "--preset", "tiny", "--seed", "1", "--months", "2", "--out", str(other),
            "--params", _params_file(tmp_path)]
    assert cli.main(argv) == 1
    assert not (other / "tiny-s1-m2").exists()
    assert not list(other.glob("*.tmp")) if other.exists() else True

    # Copilot batches go through the same check (4.5): failing only for them stops the run too
    monkeypatch.setattr(render, "batch_failures", lambda records, today=None: [
        "forced"] if any(getattr(r, "provider", "") == "github_copilot" for r in records) else [])
    only = tmp_path / "forced_copilot"
    argv = ["generate", "--preset", "tiny", "--seed", "1", "--months", "2", "--out", str(only),
            "--params", _params_file(tmp_path)]
    assert cli.main(argv) == 1
    assert not (only / "tiny-s1-m2").exists()
    assert not list(only.glob("*.tmp")) if only.exists() else True


def test_done_check_S15_negative_control_without_the_patch_it_succeeds(tmp_path, monkeypatch):
    argv = ["generate", "--preset", "tiny", "--seed", "1", "--months", "2", "--out", str(tmp_path / "ok"),
            "--params", _params_file(tmp_path)]
    assert cli.main(argv) == 0
    # a patch that fails only another provider's batches does not stop a Copilot run
    from meterline_sim import render

    monkeypatch.setattr(render, "batch_failures", lambda records, today=None: [
        "forced"] if any(getattr(r, "provider", "") == "nosuch" for r in records) else [])
    argv = ["generate", "--preset", "tiny", "--seed", "1", "--months", "2", "--out", str(tmp_path / "ok2"),
            "--params", _params_file(tmp_path)]
    assert cli.main(argv) == 0


# ---- S16 ---------------------------------------------------------------------

FORBIDDEN = re.compile(
    r"datetime\.now|datetime\.utcnow|date\.today|time\.time|\buuid|os\.urandom|\bsecrets\b|SystemRandom"
)
FAKER = re.compile(r"^\s*(import|from)\s+faker", re.M | re.I)
MODULE_RANDOM = re.compile(r"\brandom\.(?!Random\b)[A-Za-z_]+\s*\(")
FLOAT_WORD = re.compile(r"\bfloat\b")


def scan_source(root):
    problems = []
    for path in sorted(Path(root).rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern, label in ((FORBIDDEN, "forbidden name"), (FAKER, "faker"), (MODULE_RANDOM, "module-level random")):
            for match in pattern.finditer(text):
                problems.append("%s: %s %r" % (path.name, label, match.group(0)))
    return problems


def scan_money_files(root):
    problems = []
    for name in ("prices.py", "billing.py"):
        path = Path(root) / name
        if not path.is_file():
            problems.append(name + " is missing")
        elif FLOAT_WORD.search(path.read_text(encoding="utf-8")):
            problems.append(name + " uses float")
    return problems


EMAIL = re.compile(r"[^\s,\"']+@[^\s,\"']+")


def scan_identities(folder):
    problems = []
    for name in ("org/directory.csv", "org/identities.csv"):
        with open(Path(folder) / name, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                for value in row.values():
                    if "@" in value and not value.endswith("@example.com"):
                        problems.append("%s: %s" % (name, value))
    for _batch, rec in load_records(folder):
        email = getattr(rec, "actor_email", None)
        if email is not None and not email.endswith("@example.com"):
            problems.append("record email " + email)
        login = getattr(rec, "actor_login", None)
        if login is not None and not re.match(r"^[a-z]+-[a-z]+$", login):
            problems.append("record login " + login)
    with open(Path(folder) / "org/identities.csv", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["actor_login"] and not re.match(r"^[a-z]+-[a-z]+$", row["actor_login"]):
                problems.append("identity login " + row["actor_login"])
    return problems


def test_done_check_S16_source_scan_is_clean():
    assert scan_source(SRC_DIR) == []
    assert scan_money_files(SRC_DIR) == []


def test_done_check_S16_negative_control_fixture_is_flagged(tmp_path):
    (tmp_path / "bad.py").write_text("import datetime\nx = datetime.now()\n", encoding="utf-8")
    assert scan_source(tmp_path) != []
    (tmp_path / "bad2.py").write_text("import random\nx = random.random()\n", encoding="utf-8")
    assert any("module-level random" in p for p in scan_source(tmp_path))
    (tmp_path / "prices.py").write_text("x = float(1)\n", encoding="utf-8")
    assert scan_money_files(tmp_path) != []


def test_done_check_S16_emails_and_logins_are_generic(tiny_run, vertical_run, tmp_path):
    assert scan_identities(tiny_run) == []
    assert scan_identities(vertical_run) == []


def test_done_check_S16_negative_control_foreign_domain_is_flagged(tiny_run, tmp_path):
    from sim_helpers import copy_run, mutate_csv

    broken = copy_run(tiny_run, tmp_path)

    def foreign(rows):
        rows[0]["email"] = "someone" + "@" + "mail.com"  # built, so no address literal sits in the file
        return rows

    mutate_csv(broken / "org" / "directory.csv", foreign)
    assert scan_identities(broken) != []

    odd_login = copy_run(tiny_run, tmp_path / "login")

    def bad_login(rows):
        rows[0]["actor_login"] = "Not_A_Login1"
        return rows

    mutate_csv(odd_login / "org" / "identities.csv", bad_login)
    assert scan_identities(odd_login) != []
