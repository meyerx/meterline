import pytest

from meterline_anthropic_admin.testing import Clock, FakeAPI
from admin_helpers import FAKE_KEY



@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", FAKE_KEY)
    return FAKE_KEY


@pytest.fixture
def fake_clock():
    return Clock()


@pytest.fixture
def make_api(fake_clock):
    def make(usage_rows, cost_rows=None, **kw):
        return FakeAPI(usage_rows, cost_rows, api_key=FAKE_KEY, clock=fake_clock, **kw)

    return make


@pytest.fixture
def data_dir(tmp_path):
    path = tmp_path / "meterline-data"
    path.mkdir()
    return path
