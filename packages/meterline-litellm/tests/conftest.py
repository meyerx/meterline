import pytest

from meterline_litellm.testing import FakeProxy

FAKE_KEY = "sk-synthetic-not-a-real-key"
BASE_URL = "https://proxy.example.com"


class FakeClock:
    """Controllable time source; ``sleep`` advances it and is recorded."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds) -> None:
        self.sleeps.append(seconds)
        self.now += max(seconds, 0)


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LITELLM_BASE_URL", BASE_URL)
    return FAKE_KEY


@pytest.fixture
def fake_clock():
    return FakeClock()


@pytest.fixture
def make_proxy(fake_clock):
    def make(rows, **kw):
        return FakeProxy(rows, api_key=FAKE_KEY, clock=fake_clock, **kw)

    return make


@pytest.fixture
def data_dir(tmp_path):
    path = tmp_path / "meterline-data"
    path.mkdir()
    return path
