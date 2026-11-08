"""B5: the examples run in CI on simulated data and recorded shapes, and the export ships them.

Each example folder has a README with goal, commands, expected output and what it reads; examples 01, 02 and 05 are run here
on a build of the ``tiny`` preset; example 03 runs the meterline-job package, whose tests (packages/meterline-job/tests) CI runs;
example 04 is a container that the CI container job builds and runs (tools/test_example_04_container.py checks its files);
examples 06 and 07 (a DuckDB warehouse layout, per-person messages) have their own tests (tools/test_example_06_warehouse.py,
tools/test_example_07_messages.py); the
CI job runs the "Run it" block of every example README that needs no container.

Run: python3 -m pytest -q tools/test_examples.py
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import export_public as ep  # noqa: E402
import readme_block  # noqa: E402

meterline_gold = pytest.importorskip("meterline_gold")
meterline_sim = pytest.importorskip("meterline_sim")

ROOT = Path(__file__).resolve().parents[1]


def _kpi_names():
    """The KPIs of examples/01-sim-to-kpis/kpis.sql, read from the file so a new one needs no edit here."""
    return re.findall(r"^-- name: (\S+)$", (ROOT / "examples" / "01-sim-to-kpis" / "kpis.sql").read_text(encoding="utf-8"), re.M)
EXAMPLES = sorted(p.name for p in (ROOT / "examples").iterdir() if p.is_dir())
KEY = "a-key-of-at-least-32-bytes-that-you-keep-out-of-git"


def test_the_examples_exist():
    assert EXAMPLES == ["01-sim-to-kpis", "02-exec-report", "03-daily-job", "04-container", "05-dashboard", "06-warehouse-duckdb", "07-person-messages"]


@pytest.mark.parametrize("example", EXAMPLES)
def test_each_readme_has_goal_reads_run_block_and_expected_output(example):
    text = (ROOT / "examples" / example / "README.md").read_text(encoding="utf-8")
    assert "**Goal.**" in text and "**Reads.**" in text and "**Expected output.**" in text
    assert readme_block.block_after(text, "Run it").strip()


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    run = meterline_sim.generate(preset="tiny", seed=7, months=6, out=tmp_path_factory.mktemp("ex_sim"))
    base = Path(meterline_gold.__file__).resolve().parent / "examples"
    out = tmp_path_factory.mktemp("ex_gold") / "gold"
    meterline_gold.build(silver=run, org=Path(run) / "org", policy=base / "policy.example.yaml",
                         prices=base / "prices.example.yaml", key=KEY.encode(), out=out)
    return out


def test_done_check_B5_example_01_exits_0_and_prints_every_kpi(tiny):
    done = subprocess.run([sys.executable, str(ROOT / "examples" / "01-sim-to-kpis" / "kpis.py"), str(tiny)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert len(re.findall(r"^== ", done.stdout, re.M)) == len(_kpi_names())


def test_done_check_B5_example_02_exits_0_and_the_page_names_no_private_file(tiny, tmp_path):
    page = tmp_path / "report.html"
    done = subprocess.run([sys.executable, str(ROOT / "examples" / "02-exec-report" / "report.py"), str(tiny),
                           "--out", str(page)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    text = page.read_text(encoding="utf-8")
    assert "withheld" in text and "<table>" in text
    assert not re.search(r"central/|delivery/|manifest", text)


def test_example_02_refuses_a_file_that_is_not_servable(tiny):
    # negative control: asking the report's reader for a central file is refused before it is opened
    sys.path.insert(0, str(ROOT / "examples" / "02-exec-report"))
    try:
        import report
        with pytest.raises(PermissionError):
            report.read_served(tiny, "central/person_month.csv")
        with pytest.raises(PermissionError):
            report.read_served(tiny, "manifest.json")
    finally:
        sys.path.pop(0)


def test_done_check_example_05_renders_one_audience_and_exits_0(tiny, tmp_path):
    # the gold root of the job has a ``published`` link; the example reads nothing else
    (tmp_path / "published").symlink_to(tiny)
    manager = sorted(p.name for p in (tiny / "manager").iterdir())[0]
    done = subprocess.run([sys.executable, str(ROOT / "examples" / "05-dashboard" / "dashboard.py"), str(tmp_path),
                           "--audience", "manager:" + manager, "--check"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok: rendered manager/" + manager


def test_example_05_exits_2_when_published_is_absent(tmp_path):
    done = subprocess.run([sys.executable, str(ROOT / "examples" / "05-dashboard" / "dashboard.py"), str(tmp_path),
                           "--audience", "exec", "--check"], capture_output=True, text=True)
    assert done.returncode == 2 and "published/ is absent" in done.stderr


def test_ci_runs_the_run_it_block_of_every_example():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for example in EXAMPLES:
        assert "examples/%s/README.md" % example in ci, example


def test_the_export_ships_the_examples():
    assert "examples" in ep.INCLUDE
