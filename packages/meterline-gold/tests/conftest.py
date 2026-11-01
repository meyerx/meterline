"""Fixtures for the meterline-gold done-checks (spec section 14).

Everything here builds from synthetic data only: the hand-built fixture of docs/gold-fixture/ or
meterline-sim output. ``meterline_gold`` is imported inside the fixtures, so a test fails with
ModuleNotFoundError while the package does not exist.
"""

import json
import time
from pathlib import Path

import pytest

from gold_helpers import FIXTURE, KEY, fixture_inputs


def pytest_configure(config):
    config.addinivalue_line("markers", "timing: a speed check; run in the done gate, skipped when CI is set")


@pytest.fixture(scope="session")
def fixture_out(tmp_path_factory):
    import meterline_gold

    out = tmp_path_factory.mktemp("gold_fixture") / "gold"
    inp = fixture_inputs()
    meterline_gold.build(
        silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
        invoice=inp["invoice"], key=KEY, out=out,
    )
    return out


class SimRun:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.silver = self.folder
        self.org = self.folder / "org"
        self.truth = json.loads((self.folder / "ground_truth.json").read_text(encoding="utf-8"))


def _example_files():
    import meterline_gold

    base = Path(meterline_gold.__file__).resolve().parent / "examples"
    return base / "policy.example.yaml", base / "prices.example.yaml"


@pytest.fixture(scope="session")
def sim_runs(tmp_path_factory):
    meterline_sim = pytest.importorskip("meterline_sim")

    runs = {}
    for preset in ("tiny", "vertical"):
        out = tmp_path_factory.mktemp("sim_" + preset)
        folder = meterline_sim.generate(preset=preset, seed=7, months=6, out=out)
        runs[preset] = SimRun(folder)
    return runs


@pytest.fixture(scope="session")
def sim_builds(sim_runs, tmp_path_factory):
    """preset -> (gold folder, seconds the build took)."""
    import meterline_gold

    policy, prices = _example_files()
    builds = {}
    for preset, run in sim_runs.items():
        out = tmp_path_factory.mktemp("gold_" + preset) / "gold"
        started = time.perf_counter()
        meterline_gold.build(silver=run.silver, org=run.org, policy=policy, prices=prices, key=KEY, out=out)
        builds[preset] = (out, time.perf_counter() - started)
    return builds


@pytest.fixture(scope="session")
def example_files():
    return _example_files()


@pytest.fixture(scope="session")
def fixture_dir():
    return FIXTURE
