"""B: exit code 3 (permanent refusal) and ``failure_kind`` for meterline_anthropic_admin, through ``run`` and its fake API."""

from datetime import date, datetime, timezone

import httpx
import pytest

from meterline_anthropic_admin import run
from meterline_anthropic_admin.testing import FakeAPI

NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)

from meterline_anthropic_admin.testing import COST_PATH, USAGE_PATH


def _fake():
    return FakeAPI()


def _fail(fake, status, times=50):
    for path in (USAGE_PATH, COST_PATH):
        fake.fail(path, status, times=times)


def _run(tmp_path, fake, transport=None, credentials=None, **over):
    options = dict(since="2026-09-01", provider_account_id="org-one")
    options.update(over)
    creds = {"ANTHROPIC_ADMIN_KEY": fake.api_key} if credentials is None else credentials
    return run("backfill", data_dir=tmp_path / "out", credentials=creds, transport=transport or fake.transport,
               sleep=lambda s: None, now=lambda: NOW, jitter=lambda: 0, **options)


BAD_OPTIONS = {"rate_per_minute": 0}



def _timeout_transport():
    def refuse(request):
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    return httpx.MockTransport(refuse)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_a_permanent_refusal_exits_3_and_says_what_to_fix(tmp_path, capsys, status):
    fake = _fake()
    _fail(fake, status)
    result = _run(tmp_path, fake)
    assert result.exit_code == 3 and result.failure_kind == "permanent" and result.http_status == status
    assert "permanent:" in capsys.readouterr().err
    assert len(fake.log) == 1  # a refusal is not retried


@pytest.mark.parametrize("status", [500, 503])
def test_a_server_failure_exits_1_and_says_to_re_run(tmp_path, capsys, status):
    fake = _fake()
    _fail(fake, status)
    result = _run(tmp_path, fake)
    assert result.exit_code == 1 and result.failure_kind == "transient" and result.http_status == status
    assert "transient: re-run later" in capsys.readouterr().err


def test_a_timeout_exits_1_as_transient(tmp_path):
    result = _run(tmp_path, _fake(), transport=_timeout_transport())
    assert (result.exit_code, result.failure_kind) == (1, "transient")


def test_bad_options_exit_2_as_input(tmp_path):
    result = _run(tmp_path, _fake(), **BAD_OPTIONS)
    assert (result.exit_code, result.failure_kind, result.http_status) == (2, "input", None)


def test_no_key_exits_2_as_input(tmp_path):
    result = _run(tmp_path, _fake(), credentials={})
    assert (result.exit_code, result.failure_kind) == (2, "input")


def test_a_good_run_has_failure_kind_none(tmp_path):
    result = _run(tmp_path, _fake())
    assert (result.exit_code, result.failure_kind, result.http_status) == (0, "none", None)
