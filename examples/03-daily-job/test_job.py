"""The daily job end to end against the connectors' public fake APIs (no key, no network).

The fakes serve invented data in the documented response shapes. This proves the job's wiring: connectors run through
their Python entry points, silver is written, gold is built, status lines are JSON, one failed provider does not stop
the others and the exit code says so. It does not prove live authentication, live response shapes or rate limits.
"""
import importlib.util
import io
import json
import os
import shutil
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

import meterline_anthropic.testing as anthropic_fake
import meterline_anthropic_admin.testing as admin_fake
import meterline_copilot.testing as copilot_fake
import meterline_gold
import meterline_litellm.testing as litellm_fake
import meterline_sim

HERE = Path(__file__).resolve().parent
NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
NOT_THE_ENVIRONMENT_KEY = "another-synthetic-value-than-the-one-in-the-environment"
SPEC = importlib.util.spec_from_file_location("daily_job", HERE / "job.py")
job = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(job)


@pytest.fixture(scope="module")
def sim_org(tmp_path_factory):
    run = meterline_sim.generate(preset="tiny", seed=7, months=6, out=tmp_path_factory.mktemp("job_sim"))
    return Path(run) / "org"


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ANALYTICS_KEY", "synthetic-analytics-key")
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", admin_fake.SAMPLE_KEY)
    monkeypatch.setenv("LITELLM_API_KEY", litellm_fake.DEFAULT_API_KEY)
    monkeypatch.setenv("LITELLM_BASE_URL", "https://proxy.example.com")
    monkeypatch.setenv("COPILOT_TOKEN", copilot_fake.SAMPLE_TOKEN)
    monkeypatch.setenv("METERLINE_GOLD_KEY", "a-key-of-at-least-32-bytes-that-you-keep-out-of-git")


def config(tmp_path, org):
    base = Path(meterline_gold.__file__).resolve().parent / "examples"
    return {
        "data_root": str(tmp_path / "data"), "gold_root": str(tmp_path / "gold"),
        "gold": {"org": str(org), "policy": str(base / "policy.example.yaml"), "prices": str(base / "prices.example.yaml")},
        "connector": [
            {"name": "claude", "kind": "anthropic", "since": "2026-09-01"},
            {"name": "api-usage", "kind": "anthropic_admin", "provider_account_id": "my-org", "since": "2026-09-01"},
            {"name": "proxy", "kind": "litellm", "proxy_name": "my-proxy", "since": "2026-09-01"},
            {"name": "copilot", "kind": "copilot", "level": "organization", "slug": "my-org", "since": "2026-09-01"},
        ],
    }


def transports():
    return {"claude": anthropic_fake.FakeAPI().transport, "api-usage": admin_fake.FakeAPI().transport,
            "proxy": litellm_fake.FakeAPI().transport, "copilot": copilot_fake.FakeAPI().transport}


def run(tmp_path, org, days=0, **kw):
    out = io.StringIO()
    code = job.run_job(config(tmp_path, org), transports=kw.pop("transports", None) or transports(),
                       now=lambda: NOW + timedelta(days=days), sleep=lambda _s: None, out=out)
    return code, [json.loads(line) for line in out.getvalue().splitlines()]


def silver_kinds(tmp_path, name):
    records = tmp_path / "data" / name / "records"
    return sorted(p.name for p in records.iterdir() if any(p.glob("*.jsonl"))) if records.is_dir() else []


def test_every_connector_runs_and_gold_is_built(tmp_path, sim_org):
    code, lines = run(tmp_path, sim_org)
    assert code == 0, lines
    assert [l["step"] for l in lines] == ["claude", "api-usage", "proxy", "copilot", "gold"]
    assert all(l["ok"] for l in lines)
    for name in ("claude", "api-usage", "proxy", "copilot"):
        assert silver_kinds(tmp_path, name), name
    folder = tmp_path / "gold" / "2026-09-15"
    assert (folder / "manifest.json").is_file() and (folder / "exec" / "costs_month.csv").is_file()


def test_a_failing_provider_exits_non_zero_and_the_others_still_write_silver(tmp_path, sim_org):
    broken = dict(transports(), proxy=httpx.MockTransport(lambda request: httpx.Response(500)))
    code, lines = run(tmp_path, sim_org, transports=broken)
    by_step = {l["step"]: l for l in lines}
    assert code != 0
    assert by_step["proxy"]["ok"] is False and by_step["proxy"]["exit_code"] == 1
    for name in ("claude", "api-usage", "copilot"):
        assert by_step[name]["ok"] is True and silver_kinds(tmp_path, name), name
    assert not silver_kinds(tmp_path, "proxy")
    assert by_step["gold"]["ok"] is True  # gold still runs on the silver that exists


def test_a_second_run_uses_daily_and_a_missing_key_is_reported(tmp_path, sim_org, monkeypatch):
    assert run(tmp_path, sim_org)[0] == 0
    code, lines = run(tmp_path, sim_org, days=1)  # the clock moves on: a run in the same second is refused
    assert code == 0 and all(l["ok"] for l in lines)
    monkeypatch.delenv("COPILOT_TOKEN")
    shutil.rmtree(tmp_path / "data" / "copilot")
    code, lines = run(tmp_path, sim_org, days=2)
    assert code != 0 and {l["step"]: l["exit_code"] for l in lines if "exit_code" in l}["copilot"] == 2


def test_the_status_lines_name_no_key(tmp_path, sim_org):
    _, lines = run(tmp_path, sim_org)
    text = json.dumps(lines)
    for secret in (admin_fake.SAMPLE_KEY, litellm_fake.DEFAULT_API_KEY, copilot_fake.SAMPLE_TOKEN):
        assert secret not in text


# ---- Improvements 1 D: which command to run, what to retry, what to stop on --------------------------------------


def step(lines, name):
    return {l["step"]: l for l in lines}[name]


def test_a_second_run_after_a_published_backfill_is_a_daily(tmp_path, sim_org):
    _, first = run(tmp_path, sim_org)
    _, second = run(tmp_path, sim_org, days=1)
    assert [l["command"] for l in first if "command" in l] == ["backfill"] * 4
    assert [l["command"] for l in second if "command" in l] == ["daily"] * 4


def test_a_failed_first_backfill_is_followed_by_a_backfill_not_a_daily(tmp_path, sim_org):
    down = dict(transports(), proxy=httpx.MockTransport(lambda request: httpx.Response(503)))
    code, lines = run(tmp_path, sim_org, transports=down)
    assert step(lines, "proxy")["command"] == "backfill" and step(lines, "proxy")["failure_kind"] == "transient"
    # the failed run left a state.json behind: it is not proof that a backfill finished
    assert (tmp_path / "data" / "proxy" / "state.json").is_file()
    assert not list((tmp_path / "data" / "proxy" / "runs").glob("*.json")) if (tmp_path / "data" / "proxy" / "runs").is_dir() else True
    code, lines = run(tmp_path, sim_org, days=1)  # the provider is back
    assert code == 0, lines
    assert step(lines, "proxy")["command"] == "backfill" and step(lines, "proxy")["ok"] is True
    manifests = [json.loads(p.read_text(encoding="utf-8")) for p in (tmp_path / "data" / "proxy" / "runs").glob("*.json")]
    assert [m["command"] for m in manifests] == ["backfill"]


def test_a_401_is_not_retried_and_raises_an_alert(tmp_path, sim_org):
    refused = litellm_fake.FakeAPI(api_key=NOT_THE_ENVIRONMENT_KEY)
    code, lines = run(tmp_path, sim_org, transports=dict(transports(), proxy=refused.transport))
    line = step(lines, "proxy")
    assert (line["exit_code"], line["failure_kind"], line["http_status"], line["attempts"]) == (3, "permanent", 401, 1)
    assert "alert" in line and "permanent" in line["alert"]
    assert len(refused.log) == 1  # one request: a refusal is never retried
    assert code == 3  # the job's own exit code says a person must act
    for name in ("claude", "api-usage", "copilot"):
        assert step(lines, name)["ok"] is True  # the other providers still run


def test_a_503_is_retried_once_and_the_wait_goes_through_sleep(tmp_path, sim_org):
    flaky = litellm_fake.FakeAPI()
    flaky.fail("/user/daily/activity", 503, times=6)  # the six tries of one run; the second run finds the API back
    slept = []
    out = io.StringIO()
    config_ = config(tmp_path, sim_org)
    code = job.run_job(config_, transports=dict(transports(), proxy=flaky.transport), now=lambda: NOW,
                       sleep=slept.append, out=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    line = step(lines, "proxy")
    assert code == 0 and line["ok"] is True and line["attempts"] == 2 and line["failure_kind"] == "none", lines
    assert 300 in slept  # the job's own wait between attempts, on the injected sleep


def test_an_input_error_is_not_retried_either(tmp_path, sim_org, monkeypatch):
    monkeypatch.delenv("COPILOT_TOKEN")
    code, lines = run(tmp_path, sim_org)
    line = step(lines, "copilot")
    assert (line["exit_code"], line["failure_kind"], line["attempts"]) == (2, "input", 1)
    assert code == 2


def test_every_connector_step_names_its_failure_kind(tmp_path, sim_org):
    _, lines = run(tmp_path, sim_org)
    for line in lines:
        if line["step"] != "gold":
            assert line["failure_kind"] == "none" and line["attempts"] == 1 and line["command"] == "backfill", line


@pytest.mark.parametrize("umask", [0o022, 0o000], ids=["umask022", "umask000"])
def test_output_private_example_03(tmp_path, umask):
    """The staged silver copies (they hold actor_email) are 0600 in folders 0700, also under a data root the operator
    made with the usual 0755."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    os.chmod(data_root, 0o755)
    for name in ("claude", "proxy"):
        for kind in ("usage", "seats"):
            folder = data_root / name / "records" / kind
            folder.mkdir(parents=True)
            (folder / "batch-1.jsonl").write_text('{"actor_email": "alex.archer@example.com"}\n', encoding="utf-8")
    old = os.umask(umask)
    try:
        silver = job.stage_silver(data_root, ["claude", "proxy"], data_root / "silver")
        assert os.umask(umask) == umask, "the umask was left changed after the run"
    finally:
        os.umask(old)
    seen = [silver, *silver.rglob("*")]
    assert sum(p.is_file() for p in seen) == 4
    bad = ["%s %o" % (p.relative_to(data_root), stat.S_IMODE(p.stat().st_mode)) for p in seen
           if stat.S_IMODE(p.stat().st_mode) != (0o700 if p.is_dir() else 0o600)]
    assert not bad, "not private under umask %03o: %s" % (umask, "; ".join(bad))
