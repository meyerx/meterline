"""R-3 of the integrate part 2 spec: example 07 writes one message per person, and sending is locked.

The figures come from ``published/person/<key>/`` and the name and address from ``builds/<latest>/delivery/directory.csv`` of the
gold root the job leaves (``latest`` is the build that ``published/central/published.json`` names). The checks build that gold root
with the job's three steps, give person A and person B each a sentinel figure, name and address, write the messages, and then look
for B's sentinels in every part and header of A's message. They also check that a key is nowhere in a message or a file name, that
nothing is written outside ``--out`` and no ``.eml`` or ``directory.csv`` is tracked by git, that the default path runs with
``boto3`` absent, that ``--send`` is refused without both switches and when ``CI`` is set, and that pointing the example at
``published/`` fails. The negative control plants a join on the wrong column and the same check must catch it.

Run: python3 -m pytest -q tools/test_example_07_messages.py
"""
import ast
import csv
import email
import email.policy
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

meterline_job = pytest.importorskip("meterline_job")
pytest.importorskip("meterline_sim")

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "07-person-messages"
sys.path.insert(0, str(EXAMPLE_DIR))
import person_messages as pm  # noqa: E402

KEY = "a-key-of-at-least-32-bytes-that-you-keep-out-of-git"
A_NAME, A_MAIL, A_COST, A_REQUESTS = "Sentinelalpha", "sentinel-alpha@example.com", "27182.81", "1234567"
B_NAME, B_MAIL, B_COST, B_REQUESTS = "Sentinelbeta", "sentinel-beta@example.com", "31415.92", "7654321"
EVIL_MAIL = "evil-recipient@example.com"
MONTH = "2026-06"  # the latest month the demo freezes


def load_example_04(name):
    spec = importlib.util.spec_from_file_location("example_04_" + name, ROOT / "examples" / "04-container" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rewrite_csv(path, changes):
    """Rewrite a CSV file in place: ``changes(row_dict)`` returns the new row, or None to drop it."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header, rows = reader.fieldnames, list(reader)
    out = [changes(dict(r)) for r in rows]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(r for r in out if r is not None)


@pytest.fixture(scope="module")
def gold(tmp_path_factory, monkeypatch_module):
    """The gold root of the job's three steps on simulated data; persons A and B carry sentinels; one person is not in the directory
    and one has a name that tries to add a header."""
    base = tmp_path_factory.mktemp("m_route")
    config_dir, data_root, gold_root = base / "config", base / "data", base / "gold"
    for folder in (config_dir, data_root, gold_root):
        folder.mkdir()
    assert load_example_04("prepare_demo").main([str(config_dir), str(data_root), str(gold_root)]) == 0
    monkeypatch_module.setenv("METERLINE_GOLD_KEY", KEY)
    config = meterline_job.load_config(config_dir / "job.toml")
    for step in ("silver", "gold", "publish"):
        assert meterline_job.run_job(config, step=step) == 0, step
    published = (gold_root / "published").resolve()
    persons = sorted(p.name for p in (published / "person").iterdir())
    a, b, missing, evil = persons[:4]
    latest = json.loads((published / "central" / "published.json").read_text(encoding="utf-8"))["latest_build"]
    directory = gold_root / "builds" / latest / "delivery" / "directory.csv"

    def directory_row(row):
        if row["person_key"] == a:
            row.update(given_name=A_NAME, email=A_MAIL)
        elif row["person_key"] == b:
            row.update(given_name=B_NAME, email=B_MAIL)
        elif row["person_key"] == missing:
            return None  # a key that the directory does not know
        elif row["person_key"] == evil:
            row.update(given_name="Mallory\nBcc: " + EVIL_MAIL)
        return row

    rewrite_csv(directory, directory_row)
    for key, cost, requests in ((a, A_COST, A_REQUESTS), (b, B_COST, B_REQUESTS)):
        def figures(row, cost=cost, requests=requests):
            if row["month"] == MONTH:
                row.update(cost_ref=cost, requests=requests)
            return row
        rewrite_csv(published / "person" / key / "person_month.csv", figures)
    return gold_root, {"a": a, "b": b, "missing": missing, "evil": evil, "persons": persons}


@pytest.fixture(scope="module")
def monkeypatch_module():
    with pytest.MonkeyPatch.context() as mp:
        yield mp


def run(gold_root, out, *extra, env=None):
    """pm.main with CI and the confirmation switch unset unless ``env`` sets them; returns the exit code."""
    with pytest.MonkeyPatch.context() as mp:
        for name in ("CI", "METERLINE_MAIL_CONFIRM"):
            mp.delenv(name, raising=False)
        for name, value in (env or {}).items():
            mp.setenv(name, value)
        return pm.main([str(gold_root), "--out", str(out), *extra])


@pytest.fixture(scope="module")
def messages(gold, tmp_path_factory):
    gold_root, who = gold
    out = tmp_path_factory.mktemp("m_out") / "mail-out"
    assert run(gold_root, out) == 0
    return out


def parse(path):
    return email.message_from_bytes(Path(path).read_bytes(), policy=email.policy.default)


def message_to(out, address):
    found = [f for f in sorted(out.glob("*.eml")) if address in str(parse(f)["To"])]
    assert len(found) == 1, (address, len(found))
    return found[0]


def everything_in(path):
    """Every header and every decoded part of a message, and the raw bytes: what a reader of the file can see."""
    msg = parse(path)
    texts = [str(v) for k, v in msg.items()]
    for part in msg.walk():
        if not part.is_multipart():
            texts.append(part.get_content() if part.get_content_maintype() == "text" else part.get_payload(decode=True).decode("utf-8", "replace"))
    return "\n".join(texts) + "\n" + Path(path).read_bytes().decode("utf-8", "replace")


def leaks_of_b_in_a(out):
    """What of person B's sentinels reaches person A's message, and what of A's own is missing (empty when the join is right)."""
    text = everything_in(message_to(out, A_MAIL))
    bad = ["B's %s" % what for what, sentinel in (("name", B_NAME), ("address", B_MAIL), ("cost", B_COST), ("requests", B_REQUESTS)) if sentinel in text]
    bad += ["A's %s missing" % what for what, sentinel in (("name", A_NAME), ("cost", A_COST), ("requests", A_REQUESTS)) if sentinel not in text]
    return bad


# ---- one message per person, with that person's own figures ----

def test_person_a_gets_a_message_with_a_figures_and_nothing_of_b(gold, messages):
    assert leaks_of_b_in_a(messages) == []
    assert leaks_of_b_in_a_reversed(messages) == []


def leaks_of_b_in_a_reversed(out):
    text = everything_in(message_to(out, B_MAIL))
    return [s for s in (A_NAME, A_MAIL, A_COST, A_REQUESTS) if s in text] + [s for s in (B_NAME, B_COST, B_REQUESTS) if s not in text]


def test_negative_control_a_join_on_the_wrong_column_is_caught(gold, tmp_path, monkeypatch):
    gold_root, who = gold
    real = pm.directory_by_key

    def by_position(path):
        """The defect: the directory rows are paired with the person folders by position, shifted by one, not by person_key."""
        rows = list(real(path).values())
        return {key: rows[(i + 1) % len(rows)] for i, key in enumerate(who["persons"])}

    monkeypatch.setattr(pm, "directory_by_key", by_position)
    out = tmp_path / "faulty"
    assert run(gold_root, out) == 0
    try:
        found = leaks_of_b_in_a(out)
    except AssertionError:  # no message, or more than one, for A's address
        found = ["A's address does not name exactly one message"]
    assert found, "the check did not see a wrong join"


def has_a_row(gold_root, key):
    """True when the person's folder holds exactly one person_month row for the demo month."""
    rows = [r for r in rewrite_rows((gold_root / "published").resolve() / "person" / key / "person_month.csv") if r["month"] == MONTH]
    return len(rows) == 1


def rewrite_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_a_person_the_directory_does_not_know_gets_no_message_and_the_summary_counts_it(gold, messages, capsys, tmp_path):
    gold_root, who = gold
    persons = who["persons"]
    # no message for the key the directory lacks or for the name that tries to add a header, and none for a person without a row
    wanted = [k for k in persons if k not in (who["missing"], who["evil"]) and has_a_row(gold_root, k)]
    files = sorted(messages.glob("*.eml"))
    assert len(files) == len(wanted) and 0 < len(files) < len(persons)
    out = tmp_path / "again"
    assert run(gold_root, out) == 0
    printed = capsys.readouterr().out
    assert "wrote %d messages" % len(wanted) in printed and "skipped %d" % (len(persons) - len(wanted)) in printed
    assert who["missing"] not in printed and who["a"] not in printed


def test_a_directory_name_that_tries_to_add_a_header_is_skipped(gold, messages):
    for f in messages.glob("*.eml"):
        assert EVIL_MAIL not in f.read_text(encoding="utf-8", errors="replace"), f.name
        assert "Bcc:" not in f.read_text(encoding="utf-8", errors="replace"), f.name


def test_no_key_is_in_a_header_a_body_or_a_file_name(gold, messages):
    gold_root, who = gold
    published = (gold_root / "published").resolve()
    keys = set(who["persons"]) | {p.name for p in (published / "manager").iterdir()}
    assert re.fullmatch(r"\d{4}\.eml", "0001.eml")
    for f in messages.iterdir():
        assert re.fullmatch(r"\d{4}\.eml", f.name), f.name
        raw = f.read_bytes().decode("utf-8", "replace")
        assert not [k for k in keys if k in raw], f.name
        assert "pk_" not in raw and "person_key" not in raw and "manager_key" not in raw, f.name


def test_a_message_names_the_month_the_person_and_the_figures_and_leaves_out_a_hidden_comparison(gold, messages):
    text = everything_in(message_to(messages, A_MAIL))
    assert MONTH in text and "Hello %s" % A_NAME in text
    assert "Active days" in text and "Requests" in text and "Estimated cost" in text
    assert "27182.81" in text and "1234567" in text


def test_the_files_are_private_to_their_owner(messages):
    assert stat.S_IMODE(messages.stat().st_mode) == 0o700
    for f in messages.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name


# ---- nothing is written outside --out; nothing sensitive is tracked ----

def tree(path):
    return sorted(str(p.relative_to(path)) for p in Path(path).rglob("*"))


def test_nothing_is_written_outside_out(gold, tmp_path, monkeypatch):
    gold_root, who = gold
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    before = tree(gold_root)
    out = tmp_path / "elsewhere" / "mail-out"
    assert run(gold_root, out) == 0
    assert tree(gold_root) == before and tree(cwd) == []
    assert sorted(p.suffix for p in out.iterdir()) == [".eml"] * len(list(out.iterdir()))
    assert not list(out.glob("**/directory*"))


def test_no_eml_and_no_directory_file_is_tracked_and_the_output_folders_are_ignored():
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True).stdout.split("\n")
    assert [f for f in listed if f.endswith(".eml")] == []
    # the one directory file that git tracks is gold's synthetic org input (example.com addresses), not a delivery directory
    assert [f for f in listed if os.path.basename(f).startswith("directory.") and f != "docs/gold-fixture/input/org/directory.csv"] == []
    assert [f for f in listed if f.startswith("delivery/") or "/delivery/" in f] == []
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split("\n")
    assert "*.eml" in ignored and "/mail-out/" in ignored


def test_a_directory_file_planted_under_published_is_never_read(gold, tmp_path):
    gold_root, who = gold
    published = (gold_root / "published").resolve()
    planted = [published / "directory.csv", published / "central" / "directory.csv"]
    for target in planted:
        target.write_text("schema_version,person_key,given_name,family_name,email\n2.0,%s,Planted,Person,planted@example.com\n" % who["a"], encoding="utf-8")
    try:
        out = tmp_path / "w"
        assert run(gold_root, out) == 0
        assert "planted@example.com" not in "".join(f.read_text(encoding="utf-8", errors="replace") for f in out.glob("*.eml"))
    finally:
        for target in planted:
            target.unlink()


def test_pointing_the_example_at_published_fails(gold, tmp_path):
    gold_root, who = gold
    assert run((gold_root / "published").resolve(), tmp_path / "w1") == 2
    assert run(gold_root / "published", tmp_path / "w2") == 2
    assert not (tmp_path / "w1").exists() and not (tmp_path / "w2").exists()


def test_the_example_refuses_an_output_inside_the_gold_root_and_a_folder_that_is_not_empty(gold, tmp_path):
    gold_root, who = gold
    assert run(gold_root, gold_root / "mail-out") == 2
    assert run(gold_root, gold_root / "published" / "mail-out") == 2
    used = tmp_path / "used"
    used.mkdir()
    (used / "0001.eml").write_text("an earlier message\n", encoding="utf-8")
    assert run(gold_root, used) == 2
    assert (used / "0001.eml").read_text(encoding="utf-8") == "an earlier message\n"


def test_the_month_is_a_frozen_month_and_nothing_else(gold, tmp_path):
    gold_root, who = gold
    assert run(gold_root, tmp_path / "w1", "--month", "2026-05") == 0
    assert all("2026-05" in parse(f)["Subject"] for f in (tmp_path / "w1").glob("*.eml"))
    for bad in ("2026-07", "2026-6", "last month", "../2026-06"):
        assert run(gold_root, tmp_path / ("bad" + str(abs(hash(bad)))), "--month", bad) == 2, bad


# ---- sending is locked behind two switches and refused in CI ----

def test_the_default_path_runs_with_boto3_absent(gold, tmp_path, monkeypatch):
    gold_root, who = gold
    monkeypatch.setitem(sys.modules, "boto3", None)  # any import of boto3 now raises ImportError
    assert run(gold_root, tmp_path / "w") == 0


def test_boto3_is_imported_on_the_send_path_only():
    tree_ = ast.parse((EXAMPLE_DIR / "person_messages.py").read_text(encoding="utf-8"))
    top_level = [n for n in tree_.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not [n for n in top_level if "boto3" in ast.dump(n)]
    inside = [f.name for f in ast.walk(tree_) if isinstance(f, ast.FunctionDef)
              and any("boto3" in ast.dump(n) for n in ast.walk(f) if isinstance(n, (ast.Import, ast.ImportFrom)))]
    assert inside == ["send_all"]


def test_send_is_refused_when_CI_is_set_even_with_the_confirmation(gold, tmp_path, monkeypatch, capsys):
    gold_root, who = gold
    monkeypatch.setitem(sys.modules, "boto3", None)
    code = run(gold_root, tmp_path / "w", "--send", env={"CI": "true", "METERLINE_MAIL_CONFIRM": "yes"})
    assert code != 0 and code == 2
    assert "CI" in capsys.readouterr().err
    assert not (tmp_path / "w").exists(), "a refused send wrote files"


@pytest.mark.parametrize("confirm", [None, "", "no", "YES", "true", "1"])
def test_send_is_refused_without_the_exact_confirmation(gold, tmp_path, monkeypatch, confirm):
    gold_root, who = gold
    monkeypatch.setitem(sys.modules, "boto3", None)
    env = {} if confirm is None else {"METERLINE_MAIL_CONFIRM": confirm}
    assert run(gold_root, tmp_path / "w", "--send", env=env) == 2
    assert not (tmp_path / "w").exists()


def test_the_confirmation_alone_does_not_send(gold, tmp_path, monkeypatch):
    gold_root, who = gold
    monkeypatch.setitem(sys.modules, "boto3", None)
    assert run(gold_root, tmp_path / "w", env={"METERLINE_MAIL_CONFIRM": "yes"}) == 0  # no --send: it only writes the files


def test_send_with_both_switches_and_no_boto3_says_how_to_install_it_and_writes_nothing(gold, tmp_path, monkeypatch, capsys):
    gold_root, who = gold
    monkeypatch.setitem(sys.modules, "boto3", None)
    assert run(gold_root, tmp_path / "w", "--send", env={"METERLINE_MAIL_CONFIRM": "yes"}) == 2
    assert "pip install boto3" in capsys.readouterr().err
    assert not (tmp_path / "w").exists()
