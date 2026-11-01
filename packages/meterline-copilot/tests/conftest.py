import pytest

from meterline_copilot.testing import FakeAPI

from copilot_helpers import FAKE_BILLING_TOKEN, FAKE_TOKEN, SLUG


class FakeClock:
    """Controllable time source: ``sleep`` advances it and is recorded. It is
    also the wall clock, so ``x-ratelimit-reset`` values are in its time."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start
        self.sleeps = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds) -> None:
        self.sleeps.append(seconds)
        self.now += max(seconds, 0)


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("COPILOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("COPILOT_BILLING_TOKEN", FAKE_BILLING_TOKEN)
    return FAKE_TOKEN


@pytest.fixture
def fake_clock():
    return FakeClock()


@pytest.fixture(params=["organization", "enterprise"])
def level(request):
    return request.param


@pytest.fixture
def make_api(fake_clock, level):
    def make(**kw):
        kw.setdefault("level", level)
        kw.setdefault("slug", SLUG)
        return FakeAPI(dataset=None, token=FAKE_TOKEN, billing_token=FAKE_BILLING_TOKEN, clock=fake_clock, **kw)

    return make


@pytest.fixture
def data_dir(tmp_path):
    path = tmp_path / "meterline-data"
    path.mkdir()
    return path
