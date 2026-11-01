import copy
import json
from pathlib import Path

import pytest

from anthropic_dataset import DATA_DIR, load_dataset
from meterline_anthropic.testing import FakeApi

FAKE_KEY = "test-key-not-real"

_DATASET_CACHE = {}


class FakeClock:
    """Controllable time source; ``sleep`` advances it and is recorded."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps = []

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += max(seconds, 0)


@pytest.fixture(autouse=True)
def fake_key_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ANALYTICS_KEY", FAKE_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return FAKE_KEY


def _cached_dataset():
    if "data" not in _DATASET_CACHE:
        _DATASET_CACHE["data"] = load_dataset()
    return _DATASET_CACHE["data"]


@pytest.fixture
def dataset():
    """A private copy of the synthetic dataset (safe to modify)."""
    return copy.deepcopy(_cached_dataset())


@pytest.fixture
def expected_totals():
    return json.loads((Path(DATA_DIR) / "expected_totals.json").read_text(encoding="utf-8"))


@pytest.fixture
def fake_clock():
    return FakeClock()


@pytest.fixture
def fake_api(fake_clock, fake_key_env):
    # FakeApi deep-copies the dataset it is given, so the cache is never mutated
    return FakeApi(_cached_dataset(), clock=fake_clock, api_key=fake_key_env)


@pytest.fixture
def data_dir(tmp_path):
    path = tmp_path / "meterline-data"
    path.mkdir()
    return path
