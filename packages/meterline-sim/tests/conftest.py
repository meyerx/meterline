import os
import sys
from pathlib import Path

import pytest

# The done-checks exercise the package source of the checkout they live in. A venv's editable
# install of meterline-sim can point at another checkout (a build worktree), and the tests would
# then run that code instead. The source folder therefore goes first on sys.path, and on
# PYTHONPATH for the subprocess runs of S01 (test_the_package_under_test_is_this_checkouts_source
# guards both).
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if sys.path[:1] != [_SRC]:
    sys.path.insert(0, _SRC)
_inherited = os.environ.get("PYTHONPATH")
os.environ["PYTHONPATH"] = _SRC if not _inherited else _SRC + os.pathsep + _inherited


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: runs the vertical preset (in the done gate)")
    config.addinivalue_line("markers", "timing: wall-time bound (in the done gate)")


@pytest.fixture(scope="session")
def tiny_run(tmp_path_factory):
    from sim_helpers import gen

    return gen(tmp_path_factory.mktemp("tiny"), "tiny", 7, 6)


@pytest.fixture(scope="session")
def vertical_run(tmp_path_factory):
    from sim_helpers import gen

    return gen(tmp_path_factory.mktemp("vertical"), "vertical", 7, 6)
