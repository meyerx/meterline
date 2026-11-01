"""A: the connector template. A package made by tools/new_connector.py passes the conformance kit against its own fake
API and has the files and README sections the contract asks for.

Run: python3 -m pytest -q tools/test_new_connector.py
"""
import json
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import new_connector  # noqa: E402

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

from test_review_f32_front_door import purpose_sentence  # noqa: E402
from test_review_f25_provider_docs import OUTLINE as REQUIRED_SECTIONS  # noqa: E402  the outline every connector README follows
from test_packaging_metadata import _ci_pin  # noqa: E402  the pins CI installs

SLOW = 900  # seconds; a generated package runs the whole kit in a subprocess


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    return new_connector.scaffold("acme-demo", tmp_path_factory.mktemp("scaffold"))


@pytest.fixture(scope="module")
def two_package(tmp_path_factory):
    return new_connector.scaffold("acme-two", tmp_path_factory.mktemp("scaffold-two"), two_endpoints=True)


def _env(package):
    return dict(os.environ, PYTHONPATH=os.pathsep.join([str(package / "src"), os.environ.get("PYTHONPATH", "")]))


def _kit(package, *args):
    """Run the package's own tests (the conformance kit) in a subprocess, as a user of the package would."""
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", *args, str(package / "tests")],
                          cwd=str(package), env=_env(package), capture_output=True, text=True, timeout=SLOW)


def _script(package, code):
    """Run ``code`` with the package importable; its last stdout line is JSON."""
    done = subprocess.run([sys.executable, "-c", code], cwd=str(package), env=_env(package), capture_output=True, text=True, timeout=SLOW)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def _plant(package, relative, old, new):
    """The planted defect: replace ``old`` by ``new`` in one file of the package (and refuse a replacement that did nothing)."""
    path = package / relative
    text = path.read_text(encoding="utf-8")
    assert old in text, "the template no longer holds %r in %s" % (old, relative)
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_the_package_has_its_files(package):
    for relative in ("pyproject.toml", "README.md", "LICENSE", "tests/test_conformance_acme_demo.py",
                     *("src/meterline_acme_demo/%s.py" % m for m in ("__init__", "cli", "client", "fetch", "normalise", "testing"))):
        assert (package / relative).is_file(), relative


@pytest.mark.parametrize("which", ["package", "two_package"])
def test_no_placeholder_is_left(which, request):
    root = request.getfixturevalue(which)
    leftovers = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and "@@" in p.read_text(encoding="utf-8")]
    assert leftovers == []
    for p in root.rglob("*.py"):
        assert "two-endpoints:" not in p.read_text(encoding="utf-8"), "a marker line is left in %s" % p.name


def test_the_metadata_names_the_package_and_its_command(package):
    meta = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert meta["name"] == "meterline-acme-demo"
    assert meta["scripts"] == {"meterline-acme-demo": "meterline_acme_demo.cli:main"}
    assert any(d.startswith("meterline>=") for d in meta["dependencies"]) and any(d.startswith("httpx") for d in meta["dependencies"])
    assert meta["urls"]["Homepage"].startswith("https://github.com/")


def test_the_readme_has_every_required_section_and_the_exit_code_table(package):
    text = (package / "README.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+?)\s*$", text, re.M)
    assert [h for h in headings if h in REQUIRED_SECTIONS] == list(REQUIRED_SECTIONS), headings
    assert "built against documented shapes" in text and "glossary.md#evidence-levels" in text and "(#known-limits)" in text
    assert "When a live response differs" in text and len(re.findall(r"^\d+\. ", text, re.M)) >= 3
    assert "**Status:**" in text and "synthetic" in text
    assert purpose_sentence() in "\n".join(text.splitlines()[:12])  # the sentence every package README opens with
    assert len(re.findall(r"^\*\*[^*\n]+\.\*\*", text.split("## Known limits")[1], re.M)) >= 2
    for kind in ("none", "transient", "input", "permanent"):
        assert "`%s`" % kind in text
    assert not re.search(r"\]\((?!https?://|#)", text), "a relative link would be dead on PyPI"


@pytest.mark.parametrize("which", ["package", "two_package"])
def test_the_kit_passes_against_the_stub(which, request):
    done = _kit(request.getfixturevalue(which))
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-1000:]
    assert re.search(r"\b16 passed\b", done.stdout), done.stdout[-500:]


def test_the_generated_sources_pass_the_committed_ruff_config(package):
    # found by wiring a generated package into packages/: tools/test_lint.py read its sources and one line was too long
    from test_lint import CONFIG, _ruff  # noqa: E402
    ruff = _ruff()
    if not ruff:
        pytest.skip("ruff is not installed: pip install ruff==0.17.0 (the CI lint job installs it)")
    done = subprocess.run([ruff, "check", "--config", str(CONFIG), "--output-format", "concise", str(package / "src")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-1000:]


def test_a_bad_name_and_an_existing_folder_are_refused(tmp_path):
    with pytest.raises(SystemExit):
        new_connector.scaffold("Not A Name", tmp_path)
    new_connector.scaffold("once", tmp_path)
    with pytest.raises(SystemExit):
        new_connector.scaffold("once", tmp_path)  # negative control: it does not write over a package


# ---- E1: negative controls of the two new kit rules, and what the template now carries ----

def test_the_unplanted_stub_passes_r15_and_r16(tmp_path):
    # the control of the controls: the planted defects below are what fail the rules, not the harness
    done = _kit(new_connector.scaffold("acme-demo", tmp_path), "-k", "R15 or R16")
    assert done.returncode == 0 and re.search(r"\b2 passed\b", done.stdout), done.stdout[-1500:]


def test_a_stub_that_reads_json_as_float_fails_r15(tmp_path):
    package = new_connector.scaffold("acme-demo", tmp_path)
    _plant(package, "src/meterline_acme_demo/fetch.py", "json.loads(text, parse_float=Decimal)", "json.loads(text)")
    done = _kit(package, "-k", "R15")
    assert done.returncode != 0, done.stdout[-1500:]
    assert re.search(r"contract rule R15 .*read as a float", done.stdout), done.stdout[-2500:]


def test_a_stub_without_the_batch_check_fails_r16(tmp_path):
    package = new_connector.scaffold("acme-demo", tmp_path)
    _plant(package, "src/meterline_acme_demo/cli.py", "batch_failures(records, today)", "[]")
    done = _kit(package, "-k", "R16")
    assert done.returncode != 0, done.stdout[-1500:]
    assert re.search(r"contract rule R16 ", done.stdout), done.stdout[-2500:]


def test_the_two_endpoint_stub_is_faulted_on_both_endpoints_and_still_publishes_both_kinds(two_package, tmp_path):
    out = _script(two_package, """
import json, tempfile
from datetime import date
from meterline_acme_two import run
from meterline_acme_two.testing import PATH_TOTALS, PATH_USAGE, FakeAPI
fake = FakeAPI()
fake.fail(PATH_USAGE, 503, times=1)
fake.fail(PATH_TOTALS, 503, times=1)
fake.fail("", 429, times=1)  # "" matches every path
result = run("backfill", since=date(2026, 9, 1), provider_account_id="org-one", data_dir=tempfile.mkdtemp() + "/d",
             credentials={"ACME_TWO_API_KEY": fake.api_key}, transport=fake.transport,
             sleep=lambda s: None, jitter=lambda: 0)
print(json.dumps({"exit": result.exit_code, "records": result.records,
                  "faulted": sorted({e.path for e in fake.log if e.status in (429, 503)}),
                  "served": sorted({e.path for e in fake.log if e.status == 200})}))
""")
    assert out["exit"] == 0, out
    assert sorted(out["records"]) == ["org_totals", "usage"] and all(n > 0 for n in out["records"].values()), out
    assert out["faulted"] == ["/v1/totals", "/v1/usage"] and out["served"] == ["/v1/totals", "/v1/usage"], out


def test_the_one_endpoint_stub_has_no_totals_endpoint(package):
    fetch = (package / "src/meterline_acme_demo/fetch.py").read_text(encoding="utf-8")
    testing = (package / "src/meterline_acme_demo/testing.py").read_text(encoding="utf-8")
    assert "/v1/totals" not in fetch + testing and "OrgTotal" not in (package / "src/meterline_acme_demo/normalise.py").read_text(encoding="utf-8")


def test_the_provider_account_is_a_required_option_kept_in_the_manifest(package):
    out = _script(package, """
import json, os, tempfile
from datetime import datetime, timezone
from meterline_acme_demo.cli import main
from meterline_acme_demo.testing import FakeAPI
fake = FakeAPI()
os.environ["ACME_DEMO_API_KEY"] = fake.api_key
now = lambda: datetime(2026, 9, 15, 6, tzinfo=timezone.utc)
base = tempfile.mkdtemp()
empty = main(["--data-dir", base + "/e", "backfill", "--since", "2026-09-01", "--provider-account-id", ""], transport=fake.transport, now=now)
ok = main(["--data-dir", base + "/d", "backfill", "--since", "2026-09-01", "--provider-account-id", "org-one"], transport=fake.transport, now=now)
manifest = json.load(open(base + "/d/runs/20260915T060000Z.json"))
try:
    main(["--data-dir", base + "/m", "backfill", "--since", "2026-09-01"], transport=fake.transport, now=now)
    missing = None
except SystemExit as exc:
    missing = exc.code
print(json.dumps({"empty": empty, "ok": ok, "missing": missing, "manifest": manifest["provider_account_id"]}))
""")
    assert out["empty"] == 2 and out["ok"] == 0 and out["missing"] == 2 and out["manifest"] == "org-one", out


def test_a_field_the_stub_does_not_know_is_reported_and_never_fatal(package):
    out = _script(package, """
import json, tempfile
from datetime import date
from meterline_acme_demo import run
from meterline_acme_demo.testing import FakeAPI
fake = FakeAPI()
fake.days["2026-09-02"][0]["surprise"] = 1
result = run("backfill", since=date(2026, 9, 1), provider_account_id="org-one", data_dir=tempfile.mkdtemp() + "/d",
             credentials={"ACME_DEMO_API_KEY": fake.api_key}, transport=fake.transport)
manifest = json.load(open(str(result.manifest_path)))
print(json.dumps({"exit": result.exit_code, "result": result.unknown_fields, "manifest": manifest["unknown_fields"]}))
""")
    want = [{"endpoint": "usage", "path": "data[].surprise", "count": 1}]
    assert out["exit"] == 0 and out["result"] == want and out["manifest"] == want, out


def test_a_generated_package_pins_what_ci_installs(package):
    extras = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["parquet"] == ["pyarrow>=17"]
    dev = [d.replace(" ", "") for d in extras["dev"]]
    assert "pytest>=8" in dev and _ci_pin("pytest-xdist") in dev and _ci_pin("ruff") in dev, dev
    assert new_connector.ci_pin("ruff") == _ci_pin("ruff") and new_connector.ci_pin("pytest-xdist") == _ci_pin("pytest-xdist")


def test_the_pin_reader_refuses_a_name_ci_does_not_pin():
    with pytest.raises(SystemExit):
        new_connector.ci_pin("no-such-tool")  # negative control: no silent empty pin


def test_the_readme_gives_the_index_form_before_the_clone_form_and_names_pending(package):
    text = (package / "README.md").read_text(encoding="utf-8")
    lines = [line for line in text.split("\n") if line.startswith("pip install ")]
    clone = [i for i, line in enumerate(lines) if line.startswith("pip install -e")]
    assert lines[0] == "pip install meterline-acme-demo" and clone and all(not line.startswith("pip install -e") for line in lines[:min(clone)])
    assert text.index(lines[0]) < text.index("**From a clone**") < text.index(lines[min(clone)])
    assert "`pending`" in text.split("## Known limits")[1].split("## Public interface")[0]


# ---- T3: what a generated package does out of the box, pinned against the contract ----

NOW = "datetime(2026, 9, 15, 6, tzinfo=timezone.utc)"  # yesterday is 2026-09-14; the fake serves 2026-09-01 to 2026-09-14


def _daily_script(state, extra_args=""):
    """Python source that writes ``state`` (a JSON text, or None for no state file) into a data dir, runs ``daily`` through the
    command line with the fake API and prints JSON of the exit code, the days the fake was asked for and stderr."""
    return """
import contextlib, io, json, os, tempfile
from datetime import datetime, timezone
from meterline_acme_demo.cli import main, REREAD_DAYS
from meterline_acme_demo.testing import FakeAPI
fake = FakeAPI()
os.environ["ACME_DEMO_API_KEY"] = fake.api_key
base = tempfile.mkdtemp() + "/d"
os.makedirs(base)
state = %r
if state is not None:
    open(base + "/state.json", "w").write(state)
err = io.StringIO()
with contextlib.redirect_stderr(err):
    code = main(["--data-dir", base, "daily", "--provider-account-id", "org-one"%s], transport=fake.transport, now=lambda: %s)
days = sorted({e.params["date"] for e in fake.log})
print(json.dumps({"exit": code, "days": days, "reread": REREAD_DAYS, "stderr": err.getvalue(), "base": base}))
""" % (state, extra_args, NOW)


def test_daily_on_a_state_with_no_loaded_day_reads_the_reread_window(package):
    # contract R04: a loaded-days key is empty after a run that published nothing, and daily must work on that state
    out = _script(package, _daily_script('{"days_loaded": [], "pending": null}'))
    assert out["exit"] == 0, out
    want = ["2026-09-%02d" % (14 - n) for n in range(out["reread"])]
    assert out["days"] == sorted(want), out


def test_daily_with_a_state_file_ignores_since_and_says_so(package):
    # contract R04 and Windows: "With a state file, --since is ignored and the run says so."
    out = _script(package, _daily_script('{"days_loaded": ["2026-09-12"], "pending": null}', ', "--since", "2026-09-01"'))
    assert out["exit"] == 0, out
    assert "--since is ignored" in out["stderr"] and "state.json" in out["stderr"], out
    message = [line for line in out["stderr"].splitlines() if "--since is ignored" in line]
    assert len(message) == 1 and "2026-09-12" not in message[0] and "synthetic-acme-demo-key" not in out["stderr"], "the message holds a value of the state or the key"
    first = min("2026-09-13", "2026-09-%02d" % (14 - (out["reread"] - 1)))  # the day after the last loaded day, or the re-read start
    assert out["days"][0] == first and "2026-09-01" not in out["days"], out


def test_daily_says_nothing_about_since_when_it_is_used_or_absent(package):
    # negative controls of the message: no state file means --since is the start; a state file without --since needs no remark
    without_state = _script(package, _daily_script(None, ', "--since", "2026-09-10"'))
    assert without_state["exit"] == 0 and "ignored" not in without_state["stderr"], without_state
    assert without_state["days"][0] == "2026-09-%02d" % min(10, 14 - (without_state["reread"] - 1)), without_state
    without_since = _script(package, _daily_script('{"days_loaded": ["2026-09-12"], "pending": null}'))
    assert without_since["exit"] == 0 and "ignored" not in without_since["stderr"], without_since


def test_run_from_python_ignores_since_with_a_state_file_and_says_so(package):
    out = _script(package, """
import contextlib, io, json, os, tempfile
from datetime import date, datetime, timezone
from meterline_acme_demo import run
from meterline_acme_demo.testing import FakeAPI
fake = FakeAPI()
base = tempfile.mkdtemp() + "/d"
os.makedirs(base)
open(base + "/state.json", "w").write('{"days_loaded": [], "pending": null}')
err = io.StringIO()
with contextlib.redirect_stderr(err):
    result = run("daily", since=date(2026, 9, 1), provider_account_id="org-one", data_dir=base,
                 credentials={"ACME_DEMO_API_KEY": fake.api_key}, transport=fake.transport,
                 now=lambda: datetime(2026, 9, 15, 6, tzinfo=timezone.utc))
print(json.dumps({"exit": result.exit_code, "days": sorted({e.params["date"] for e in fake.log}), "stderr": err.getvalue()}))
""")
    assert out["exit"] == 0 and "--since is ignored" in out["stderr"] and "2026-09-01" not in out["days"], out


# ---- T3-4: the fetcher of the stub follows the contract (raw files, paging, windows, finality, re-read days) ----

def _backfill_script(module, env, since, now, tail):
    """Python source: a backfill of the fake API from ``since`` at ``now`` into a fresh data dir; ``tail`` then prints JSON."""
    return """
import gzip, json, tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from %s import run
from %s.testing import FakeAPI
fake = FakeAPI()
base = Path(tempfile.mkdtemp()) / "d"
result = run("backfill", since=date.fromisoformat(%r), provider_account_id="org-one", data_dir=base,
             credentials={%r: fake.api_key}, transport=fake.transport, now=lambda: %s)
def lines(kind):
    return [json.loads(l) for p in sorted((base / "records" / kind).glob("*.jsonl")) for l in p.read_text().splitlines()]
%s
""" % (module, module, since, env, now, tail)


def test_a_raw_file_holds_the_request_url_pulled_at_and_every_page(package):
    # contract, "Raw page naming": gzip JSON with the request URL, pulled_at and a list pages of {status, body}
    out = _script(package, _backfill_script("meterline_acme_demo", "ACME_DEMO_API_KEY", "2026-09-14", "datetime(2026, 9, 15, 6, tzinfo=timezone.utc)", """
files = sorted(p for p in (base / "raw").rglob("*") if p.is_file())
blobs = [p.read_bytes() for p in files]
docs = [json.loads(gzip.decompress(b)) for b in blobs]
print(json.dumps({"exit": result.exit_code, "paths": [str(p.relative_to(base)) for p in files], "docs": docs,
                  "mtimes": [int.from_bytes(b[4:8], "little") for b in blobs], "key": fake.api_key}))
"""))
    assert out["exit"] == 0 and out["paths"] == ["raw/usage/2026-09-14/20260915T060000Z.json.gz"], out
    doc, = out["docs"]
    assert sorted(doc) == ["endpoint", "pages", "pulled_at", "request_url", "window"], doc
    assert doc["request_url"].endswith("/v1/usage?date=2026-09-14") and doc["pulled_at"] == "2026-09-15T06:00:00Z", doc
    assert out["key"] not in json.dumps(doc) and "page=" not in doc["request_url"], "the key or a page token is in the raw file"
    assert len(doc["pages"]) > 1 and all(sorted(p) == ["body", "status"] and p["status"] == 200 for p in doc["pages"]), doc["pages"]
    assert all(isinstance(p["body"], str) and json.loads(p["body"])["date"] == "2026-09-14" for p in doc["pages"])
    assert out["mtimes"] == [0], "the gzip header holds a time: the bytes are not deterministic"


def test_one_day_is_read_over_several_pages_and_every_row_reaches_the_records(package):
    out = _script(package, _backfill_script("meterline_acme_demo", "ACME_DEMO_API_KEY", "2026-09-14", "datetime(2026, 9, 15, 6, tzinfo=timezone.utc)", """
rows = lines("usage")
print(json.dumps({"exit": result.exit_code, "params": [e.params for e in fake.log], "rows": len(rows),
                  "want": len(fake.days["2026-09-14"]), "pages": len(json.loads(gzip.decompress(next((base / "raw").rglob("*.gz")).read_bytes()))["pages"])}))
"""))
    assert out["exit"] == 0 and out["rows"] == out["want"] == 8, out
    assert out["pages"] > 1 and len(out["params"]) == out["pages"], out
    assert "page" not in out["params"][0] and all("page" in p for p in out["params"][1:]), out["params"]
    assert all(sorted(set(p) - {"page"}) == ["date"] for p in out["params"]), "one query asks for one day"


def test_a_page_cursor_that_comes_back_stops_the_run_with_exit_2_and_publishes_nothing(package):
    out = _script(package, """
import contextlib, io, json, tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from meterline_acme_demo import run
from meterline_acme_demo.testing import FakeAPI
fake = FakeAPI()
fake.repeat_page()  # the API answers a later page with the cursor it was just given
base = Path(tempfile.mkdtemp()) / "d"
err = io.StringIO()
with contextlib.redirect_stderr(err):
    result = run("backfill", since=date(2026, 9, 14), provider_account_id="org-one", data_dir=base,
                 credentials={"ACME_DEMO_API_KEY": fake.api_key}, transport=fake.transport,
                 now=lambda: datetime(2026, 9, 15, 6, tzinfo=timezone.utc))
print(json.dumps({"exit": result.exit_code, "kind": result.failure_kind, "records": [str(p) for p in base.rglob("*.jsonl")],
                  "state": (base / "state.json").exists(), "stderr": err.getvalue(), "requests": len(fake.log)}))
""")
    assert (out["exit"], out["kind"]) == (2, "input") and out["records"] == [] and out["requests"] <= 4, out
    assert "documented shape" in out["stderr"] and "nothing was published" in out["stderr"], out


@pytest.mark.parametrize("which,module,env,kinds", [("package", "meterline_acme_demo", "ACME_DEMO_API_KEY", ["usage"]),
                                                     ("two_package", "meterline_acme_two", "ACME_TWO_API_KEY", ["org_totals", "usage"])])
def test_a_day_is_final_four_days_after_it_ended_at_midnight_utc(which, module, env, kinds, request):
    # 2026-09-10 ended at 2026-09-11 00:00 UTC; four days after that is 2026-09-15 00:00 UTC
    package = request.getfixturevalue(which)
    tail = """
by = {}
for kind in %r:
    by[kind] = {r["period_start"]: r["is_final"] for r in lines(kind)}
print(json.dumps({"exit": result.exit_code, "final": by}))
""" % kinds
    for now, final in (("datetime(2026, 9, 14, 23, 59, 59, tzinfo=timezone.utc)", False), ("datetime(2026, 9, 15, 0, 0, 0, tzinfo=timezone.utc)", True)):
        out = _script(package, _backfill_script(module, env, "2026-09-10", now, tail))
        assert out["exit"] == 0 and sorted(out["final"]) == kinds, out
        for kind in kinds:
            assert out["final"][kind]["2026-09-10"] is final, (now, kind, out)
            assert out["final"][kind]["2026-09-13"] is False, (now, kind, out)  # three days old at most: never final


def test_daily_after_a_backfill_reads_the_last_five_closed_days(package):
    out = _script(package, """
import json, tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from meterline_acme_demo import run
from meterline_acme_demo.testing import FakeAPI
base = Path(tempfile.mkdtemp()) / "d"
creds = {"ACME_DEMO_API_KEY": FakeAPI().api_key}
first = run("backfill", since=date(2026, 9, 1), provider_account_id="org-one", data_dir=base, credentials=creds,
            transport=FakeAPI().transport, now=lambda: datetime(2026, 9, 15, 6, tzinfo=timezone.utc))
fake = FakeAPI()
second = run("daily", provider_account_id="org-one", data_dir=base, credentials=creds, transport=fake.transport,
             now=lambda: datetime(2026, 9, 16, 6, tzinfo=timezone.utc))
print(json.dumps({"exit": [first.exit_code, second.exit_code], "days": sorted({e.params["date"] for e in fake.log}),
                  "loaded": json.load(open(base / "state.json"))["days_loaded"]}))
""")
    assert out["exit"] == [0, 0], out
    assert out["days"] == ["2026-09-%02d" % d for d in range(11, 16)], out  # yesterday and the four days before it


def test_an_empty_day_is_loaded_only_once_it_is_final(package):
    # contract, Re-read depth: "A day with no rows is not marked loaded until it is final, because the provider may still fill it in."
    out = _script(package, """
import json, tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from meterline_acme_demo import run
from meterline_acme_demo.testing import FakeAPI
base = Path(tempfile.mkdtemp()) / "d"
creds = {"ACME_DEMO_API_KEY": FakeAPI().api_key}
def fake():
    api = FakeAPI()
    api.days["2026-09-12"] = []  # the provider has no row for this day yet
    return api
def loaded():
    return json.load(open(base / "state.json"))["days_loaded"]
first = run("backfill", since=date(2026, 9, 10), provider_account_id="org-one", data_dir=base, credentials=creds,
            transport=fake().transport, now=lambda: datetime(2026, 9, 15, 6, tzinfo=timezone.utc))
young = loaded()
second = run("daily", provider_account_id="org-one", data_dir=base, credentials=creds, transport=fake().transport,
             now=lambda: datetime(2026, 9, 17, 6, tzinfo=timezone.utc))  # 2026-09-12 became final at 2026-09-17 00:00 UTC
print(json.dumps({"exit": [first.exit_code, second.exit_code], "young": young, "later": loaded()}))
""")
    assert out["exit"] == [0, 0], out
    assert "2026-09-12" not in out["young"] and "2026-09-11" in out["young"] and "2026-09-13" in out["young"], out
    assert "2026-09-12" in out["later"], out


def test_the_readme_names_the_defaults_and_where_to_change_them(package):
    text = (package / "README.md").read_text(encoding="utf-8")
    for needle in ("`FINAL_AFTER_DAYS`", "`REREAD_DAYS`", "`WINDOW_DAYS`", "conservative", "4 days", "5 closed days", "one day"):
        assert needle in text, needle
    for stale in ("two days before it", "final on arrival"):
        assert stale not in text, stale
