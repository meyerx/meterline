"""The daily job end to end against the connectors' public fake APIs (no key, no network).

The fakes serve invented data in the documented response shapes. This proves the job's wiring: connectors run through
their Python entry points, silver is staged, gold is built, status lines are JSON, one failed provider does not stop the
others and the exit code says so. It does not prove live authentication, live response shapes or rate limits.
"""
import io
import json
import os
import shutil
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import meterline_job
from meterline_job import job as job_module
from meterline_job.job import run_job, stage_silver

httpx = pytest.importorskip("httpx")
anthropic_fake = pytest.importorskip("meterline_anthropic.testing")
admin_fake = pytest.importorskip("meterline_anthropic_admin.testing")
copilot_fake = pytest.importorskip("meterline_copilot.testing")
litellm_fake = pytest.importorskip("meterline_litellm.testing")
meterline_sim = pytest.importorskip("meterline_sim")
meterline_gold = pytest.importorskip("meterline_gold")

NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
NOT_THE_ENVIRONMENT_KEY = "another-synthetic-value-than-the-one-in-the-environment"
STEPS = ["claude", "api-usage", "proxy", "copilot", "silver", "gold", "publish"]


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


def config(tmp_path, org, **gold):
    base = Path(meterline_gold.__file__).resolve().parent / "examples"
    return {
        "data_root": str(tmp_path / "data"), "gold_root": str(tmp_path / "gold"),
        "gold": {"org": str(org), "policy": str(base / "policy.example.yaml"), "prices": str(base / "prices.example.yaml"),
                 **gold},
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


def run(tmp_path, org, days=0, gold=None, **kw):
    out = io.StringIO()
    code = run_job(config(tmp_path, org, **(gold or {})), transports=kw.pop("transports", None) or transports(),
                   now=lambda: NOW + timedelta(days=days), sleep=lambda _s: None, out=out, **kw)
    return code, [json.loads(line) for line in out.getvalue().splitlines()]


def silver_kinds(tmp_path, name):
    records = tmp_path / "data" / name / "records"
    return sorted(p.name for p in records.iterdir() if any(p.glob("*.jsonl"))) if records.is_dir() else []


def step(lines, name):
    return {line["step"]: line for line in lines}[name]


# ---- A1: the steps, the exit code 4, what reaches gold ---------------------------------------------------------------


def test_job_runs_every_step_on_fakes(tmp_path, sim_org):
    code, lines = run(tmp_path, sim_org)
    assert code == 0, lines
    assert [line["step"] for line in lines] == STEPS
    assert all(line["ok"] for line in lines)
    folder = tmp_path / "gold" / "builds" / "2026-09-15"
    assert (folder / "manifest.json").is_file() and (folder / "exec" / "costs_month.csv").is_file()
    twins = sorted(folder.rglob("*.parquet"))
    assert twins, "gold was built without its Parquet twins"
    assert all(p.with_suffix(".csv").is_file() for p in twins)
    gold = step(lines, "gold")
    assert (gold["build"], gold["retry"], gold["exit_code"], gold["failure_kind"]) == ("2026-09-15", False, 0, "none")
    published = step(lines, "publish")
    assert (published["build"], published["exit_code"], published["frozen"], published["warnings"]) == ("2026-09-15", 0, [], [])
    assert gold["data_through"] == json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["data_through"]


class _GoldSpy:
    """Stands in for ``meterline_gold.build``: records how it was called, then raises or builds."""

    def __init__(self, error=None):
        self.calls, self.error = [], error

    def __call__(self, **kw):
        self.calls.append(kw)
        if self.error is not None:
            raise self.error
        Path(kw["out"]).mkdir(parents=True)
        (Path(kw["out"]) / "manifest.json").write_text('{"data_through": null}\n', encoding="utf-8")
        return Path(kw["out"])


def test_gold_failure_exits_4_and_is_not_retried(tmp_path, sim_org, monkeypatch):
    spy = _GoldSpy(meterline_gold.BuildFailure("assertion x failed; first offending row: ()"))
    monkeypatch.setattr(meterline_gold, "build", spy)
    code, lines = run(tmp_path, sim_org)
    gold = step(lines, "gold")
    assert code == 4, lines
    assert (gold["ok"], gold["exit_code"], gold["failure_kind"], gold["attempts"], gold["retry"]) == (False, 4, "build", 1, False)
    assert "alert" in gold and "assertion x failed" in gold["error"]
    assert len(spy.calls) == 1, "gold was run again after it failed"
    assert all(step(lines, n)["ok"] for n in ("claude", "api-usage", "proxy", "copilot", "silver"))


def test_any_other_gold_error_exits_4(tmp_path, sim_org, monkeypatch):
    monkeypatch.setattr(meterline_gold, "build", _GoldSpy(RuntimeError("disk on fire")))
    code, lines = run(tmp_path, sim_org)
    assert code == 4 and step(lines, "gold")["failure_kind"] == "build", lines


def test_gold_input_error_exits_2(tmp_path, sim_org, monkeypatch):
    monkeypatch.setattr(meterline_gold, "build", _GoldSpy(meterline_gold.InputError("prices.yaml: no such file")))
    code, lines = run(tmp_path, sim_org)
    gold = step(lines, "gold")
    assert code == 2, lines
    assert (gold["exit_code"], gold["failure_kind"]) == (2, "input") and "alert" in gold


def test_the_worst_step_decides_the_exit_code(tmp_path, sim_org, monkeypatch):
    monkeypatch.setattr(meterline_gold, "build", _GoldSpy(meterline_gold.BuildFailure("x")))
    refused = litellm_fake.FakeAPI(api_key=NOT_THE_ENVIRONMENT_KEY)
    code, lines = run(tmp_path, sim_org, transports=dict(transports(), proxy=refused.transport))
    assert step(lines, "proxy")["exit_code"] == 3 and step(lines, "gold")["exit_code"] == 4
    assert code == 4  # 0 < 1 < 2 < 3 < 4


def test_transient_connector_retried_once(tmp_path, sim_org):
    flaky = litellm_fake.FakeAPI()
    flaky.fail("/user/daily/activity", 503, times=6)  # the six tries of one run; the second run finds the API back
    slept = []
    out = io.StringIO()
    code = run_job(config(tmp_path, sim_org), transports=dict(transports(), proxy=flaky.transport), now=lambda: NOW,
                   sleep=slept.append, out=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    line = step(lines, "proxy")
    assert code == 0 and line["ok"] is True and line["attempts"] == 2 and line["failure_kind"] == "none", lines
    assert 300 in slept  # the job's own wait between attempts, on the injected sleep


def test_transient_connector_not_retried_with_no_retry(tmp_path, sim_org):
    down = httpx.MockTransport(lambda request: httpx.Response(503))
    code, lines = run(tmp_path, sim_org, transports=dict(transports(), proxy=down), no_retry=True)
    line = step(lines, "proxy")
    assert (line["attempts"], line["exit_code"], line["failure_kind"]) == (1, 1, "transient")
    assert code == 1


def test_leftover_tmp_is_cleared(tmp_path, sim_org):
    leftover = tmp_path / "gold" / "builds" / "2026-09-15.tmp"
    leftover.mkdir(parents=True)
    (leftover / "half-written.csv").write_text("x\n", encoding="utf-8")
    code, lines = run(tmp_path, sim_org)
    assert code == 0, lines
    assert not leftover.exists()
    assert (tmp_path / "gold" / "builds" / "2026-09-15" / "manifest.json").is_file()


def test_a_second_build_of_the_day_gets_its_own_folder_and_leaves_the_first_alone(tmp_path, sim_org):
    assert run(tmp_path, sim_org)[0] == 0
    first = tmp_path / "gold" / "builds" / "2026-09-15"
    before = {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}
    # the clock moves on within the day: the date of the build does not
    out = io.StringIO()
    code = run_job(config(tmp_path, sim_org), now=lambda: NOW + timedelta(hours=1), out=out, step="gold")
    gold = json.loads(out.getvalue().splitlines()[-1])
    assert code == 0 and (gold["exit_code"], gold["build"]) == (0, "2026-09-15-2"), gold
    assert (tmp_path / "gold" / "builds" / "2026-09-15-2" / "manifest.json").is_file()
    assert before == {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}


def test_parquet_and_invoice_reach_gold(tmp_path, sim_org, monkeypatch):
    spy = _GoldSpy()
    monkeypatch.setattr(meterline_gold, "build", spy)
    invoice = tmp_path / "invoice.csv"
    run(tmp_path, sim_org, gold={"invoice": str(invoice)})
    assert spy.calls[0]["parquet"] is True and spy.calls[0]["invoice"] == str(invoice)
    spy.calls.clear()
    shutil.rmtree(tmp_path / "gold")
    run(tmp_path, sim_org, days=1, gold={"parquet": False})
    assert spy.calls[0]["parquet"] is False and spy.calls[0]["invoice"] is None


def test_missing_extra_exits_2_naming_it(tmp_path, sim_org, monkeypatch):
    monkeypatch.setitem(sys.modules, "meterline_copilot", None)  # an import of it now fails, as if the extra were not installed
    code, lines = run(tmp_path, sim_org)
    line = step(lines, "copilot")
    assert (line["exit_code"], line["failure_kind"], line["attempts"]) == (2, "input", 1)
    assert "meterline-job[copilot]" in line["alert"]
    assert code == 2
    assert step(lines, "claude")["ok"] is True  # the others still run


@pytest.mark.parametrize("only", ["claude", "silver", "gold"])
def test_one_step_runs_alone(tmp_path, sim_org, only):
    assert run(tmp_path, sim_org)[0] == 0
    shutil.rmtree(tmp_path / "gold")
    code, lines = run(tmp_path, sim_org, days=1, step=only)
    assert [line["step"] for line in lines] == [only]
    assert code == 0 and lines[0]["ok"] is True, lines


def test_the_publish_step_alone_works_on_the_latest_build_and_never_freezes_on_the_normal_rule(tmp_path, sim_org):
    assert run(tmp_path, sim_org)[0] == 0
    code, lines = run(tmp_path, sim_org, days=1, step="publish")
    assert code == 0 and [line["step"] for line in lines] == ["publish"]
    assert lines[0]["build"] == "2026-09-15" and lines[0]["frozen"] == []


def test_publish_without_a_build_exits_4(tmp_path, sim_org):
    code, lines = run(tmp_path, sim_org, step="publish")
    assert code == 4 and lines[0]["failure_kind"] == "publish" and "no build" in lines[0]["error"] and "alert" in lines[0]


def test_publish_does_not_run_when_gold_did_not_build(tmp_path, sim_org, monkeypatch):
    monkeypatch.setattr(meterline_gold, "build", _GoldSpy(meterline_gold.BuildFailure("x")))
    code, lines = run(tmp_path, sim_org)
    published = step(lines, "publish")
    assert code == 4 and published["ok"] is False and "gold failed" in published["error"]
    assert (published["exit_code"], published["failure_kind"]) == (4, "skipped")  # gold's code, and the reason it did not run
    assert not (tmp_path / "gold" / "frozen").exists()


def test_an_unknown_step_is_an_input_error(tmp_path, sim_org):
    code, lines = run(tmp_path, sim_org, step="nosuch")
    assert code == 2 and lines[0]["failure_kind"] == "input" and "claude" in lines[0]["error"]


# ---- the cases of the old examples/03-daily-job/test_job.py, adapted -------------------------------------------------


def test_a_failing_provider_exits_non_zero_and_the_others_still_write_silver(tmp_path, sim_org):
    broken = dict(transports(), proxy=httpx.MockTransport(lambda request: httpx.Response(500)))
    code, lines = run(tmp_path, sim_org, transports=broken)
    by_step = {line["step"]: line for line in lines}
    assert code != 0
    assert by_step["proxy"]["ok"] is False and by_step["proxy"]["exit_code"] == 1
    for name in ("claude", "api-usage", "copilot"):
        assert by_step[name]["ok"] is True and silver_kinds(tmp_path, name), name
    assert not silver_kinds(tmp_path, "proxy")
    assert by_step["gold"]["ok"] is True  # gold still runs on the silver that exists


def test_a_second_run_uses_daily_and_a_missing_key_is_reported(tmp_path, sim_org, monkeypatch):
    assert run(tmp_path, sim_org)[0] == 0
    code, lines = run(tmp_path, sim_org, days=1)  # the clock moves on: a run in the same second is refused
    assert code == 0 and all(line["ok"] for line in lines)
    monkeypatch.delenv("COPILOT_TOKEN")
    shutil.rmtree(tmp_path / "data" / "copilot")
    code, lines = run(tmp_path, sim_org, days=2)
    assert code != 0 and {line["step"]: line["exit_code"] for line in lines if "exit_code" in line}["copilot"] == 2


def test_the_status_lines_name_no_key(tmp_path, sim_org):
    _, lines = run(tmp_path, sim_org)
    text = json.dumps(lines)
    for secret in (admin_fake.SAMPLE_KEY, litellm_fake.DEFAULT_API_KEY, copilot_fake.SAMPLE_TOKEN):
        assert secret not in text


def test_a_second_run_after_a_published_backfill_is_a_daily(tmp_path, sim_org):
    _, first = run(tmp_path, sim_org)
    _, second = run(tmp_path, sim_org, days=1)
    assert [line["command"] for line in first if line["step"] in STEPS[:4]] == ["backfill"] * 4
    assert [line["command"] for line in second if line["step"] in STEPS[:4]] == ["daily"] * 4


def _manifests(tmp_path, name):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((tmp_path / "data" / name / "runs").glob("*.json"))]


def test_the_job_runs_auto_backfill_on_the_first_day_and_daily_on_the_second(tmp_path, sim_org):
    # every connector step runs the connector's auto command; the step line names what auto chose, the manifest both
    for days, want in ((0, "backfill"), (1, "daily")):
        code, lines = run(tmp_path, sim_org, days=days, gold={"parquet": False})
        assert code == 0, lines
        for name in STEPS[:4]:
            assert step(lines, name)["command"] == want, (days, step(lines, name))
            latest = _manifests(tmp_path, name)[-1]
            assert (latest["command"], latest["requested"]) == (want, "auto"), (name, days, latest["command"], latest.get("requested"))


def test_a_failed_first_backfill_is_followed_by_a_backfill_not_a_daily(tmp_path, sim_org):
    down = dict(transports(), proxy=httpx.MockTransport(lambda request: httpx.Response(503)))
    code, lines = run(tmp_path, sim_org, transports=down)
    assert step(lines, "proxy")["command"] == "backfill" and step(lines, "proxy")["failure_kind"] == "transient"
    # the failed run left a state.json behind: it is not proof that a backfill finished
    assert (tmp_path / "data" / "proxy" / "state.json").is_file()
    assert not list((tmp_path / "data" / "proxy" / "runs").glob("*.json"))
    code, lines = run(tmp_path, sim_org, days=1, gold={"parquet": False})  # the provider is back
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


def test_an_input_error_is_not_retried_either(tmp_path, sim_org, monkeypatch):
    monkeypatch.delenv("COPILOT_TOKEN")
    code, lines = run(tmp_path, sim_org)
    line = step(lines, "copilot")
    assert (line["exit_code"], line["failure_kind"], line["attempts"]) == (2, "input", 1)
    assert code == 2


def test_every_connector_step_names_its_failure_kind(tmp_path, sim_org):
    _, lines = run(tmp_path, sim_org)
    for line in lines:
        if line["step"] not in ("silver", "gold", "publish"):
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
        silver = stage_silver(data_root, ["claude", "proxy"], data_root / "silver")
        assert os.umask(umask) == umask, "the umask was left changed after the run"
    finally:
        os.umask(old)
    seen = [silver, *silver.rglob("*")]
    assert sum(p.is_file() for p in seen) == 4
    bad = ["%s %o" % (p.relative_to(data_root), stat.S_IMODE(p.stat().st_mode)) for p in seen
           if stat.S_IMODE(p.stat().st_mode) != (0o700 if p.is_dir() else 0o600)]
    assert not bad, "not private under umask %03o: %s" % (umask, "; ".join(bad))


def test_the_package_exports_its_version_and_entry_points():
    assert meterline_job.run_job is job_module.run_job and callable(meterline_job.main)
    assert meterline_job.__version__


# ---- A2: the job and the locks -----------------------------------------------------------------------------------------

from meterline.run import data_folder_lock, file_lock  # noqa: E402


def test_the_silver_step_skips_a_locked_connector_and_exits_1(tmp_path, sim_org, capsys):
    assert run(tmp_path, sim_org)[0] == 0
    with data_folder_lock(tmp_path / "data" / "claude") as held:
        assert held is True
        code, lines = run(tmp_path, sim_org, days=1, step="silver")
    line = lines[0]
    assert code == 1 and (line["exit_code"], line["failure_kind"], line["ok"]) == (1, "transient", False)
    assert line["skipped"] == ["claude"] and "in use by another run" in line["error"]
    staged = sorted(p.name for p in (tmp_path / "data" / "silver" / "records" / "usage").glob("*.jsonl"))
    assert staged and not any(name.startswith("claude-") for name in staged)  # the others were copied
    assert "in use by another run" in capsys.readouterr().err


def test_the_gold_step_stops_when_the_gold_folder_is_locked(tmp_path, sim_org):
    (tmp_path / "gold").mkdir()
    with file_lock(tmp_path / "gold" / ".lock", "gold folder") as held:
        assert held is True
        code, lines = run(tmp_path, sim_org, step="gold")
    line = lines[0]
    assert code == 1 and (line["exit_code"], line["failure_kind"]) == (1, "transient")
    assert "in use by another run" in line["error"]
    assert not (tmp_path / "gold" / "builds").exists()  # nothing was built


def test_a_connector_whose_folder_is_locked_is_a_transient_step(tmp_path, sim_org):
    with data_folder_lock(tmp_path / "data" / "claude"):
        code, lines = run(tmp_path, sim_org, step="claude", no_retry=True)
    line = lines[0]
    assert code == 1 and (line["exit_code"], line["failure_kind"], line["attempts"]) == (1, "transient", 1)
    assert not list((tmp_path / "data" / "claude").glob("records/**/*.jsonl")) and not (tmp_path / "data" / "claude" / "state.json").exists()


def test_the_lock_files_stay_out_of_silver_and_gold(tmp_path, sim_org):
    assert run(tmp_path, sim_org)[0] == 0
    assert (tmp_path / "data" / "claude" / "runs" / ".lock").is_file()
    assert not list((tmp_path / "data" / "silver").rglob(".lock"))
    assert not list((tmp_path / "gold" / "builds" / "2026-09-15").rglob(".lock"))


def test_a_held_gold_lock_stops_gold_and_publish_alike(tmp_path, sim_org):
    (tmp_path / "gold").mkdir()
    with file_lock(tmp_path / "gold" / ".lock", "gold folder") as held:
        assert held is True
        code, lines = run(tmp_path, sim_org, step=None, no_retry=True)
    by_step = {line["step"]: line for line in lines}
    assert code == 1
    for name in ("gold", "publish"):
        assert (by_step[name]["exit_code"], by_step[name]["failure_kind"]) == (1, "transient"), by_step[name]
        assert "in use by another run" in by_step[name]["error"]
    assert not (tmp_path / "gold" / "builds").exists() and not (tmp_path / "gold" / "frozen").exists()


def test_data_complete_through_is_the_earliest_complete_through_of_the_real_connectors(tmp_path, sim_org):
    from meterline_job import publish

    cfg = config(tmp_path, sim_org)
    assert publish.data_complete_through(cfg) is None  # nothing has run: every connector says None
    assert run(tmp_path, sim_org)[0] == 0
    modules = {"anthropic": anthropic_fake, "anthropic_admin": admin_fake, "litellm": litellm_fake, "copilot": copilot_fake}
    import importlib

    days = [importlib.import_module(job_module.KINDS[s["kind"]][0]).complete_through(tmp_path / "data" / s["name"])
            for s in cfg["connector"]]
    assert all(d is None or hasattr(d, "year") for d in days) and modules
    assert publish.data_complete_through(cfg) == (None if None in days else min(days))


# ---- v0.3 hardening A-1: the published folder says which source is stale -----------------------------------------------


def test_a_proxy_that_answers_503_on_the_run_day_is_stale_in_the_published_folder(tmp_path, sim_org, monkeypatch):
    from meterline_job import publish

    real = publish.connector_complete_through
    # the fakes of the anthropic and copilot connectors serve no final day (their answer is None); the proxy keeps its own
    monkeypatch.setattr(publish, "connector_complete_through",
                        lambda spec, data_dir: real(spec, data_dir) if spec["name"] == "proxy" else (NOW - timedelta(days=1)).date())
    assert run(tmp_path, sim_org)[0] == 0
    down = dict(transports(), proxy=httpx.MockTransport(lambda request: httpx.Response(503)))
    code, lines = run(tmp_path, sim_org, days=1, transports=down, no_retry=True)
    assert code == 1 and step(lines, "proxy")["failure_kind"] == "transient"
    served = tmp_path / "gold" / "published"
    published = json.loads((served / "central" / "published.json").read_text(encoding="utf-8"))
    sources = {s["name"]: s for s in published["sources"]}
    assert list(sources) == ["claude", "api-usage", "proxy", "copilot"]
    assert (sources["proxy"]["step_ok"], sources["proxy"]["failure_kind"], sources["proxy"]["provider"]) == (False, "transient", "litellm")
    assert all(sources[name]["step_ok"] is True for name in ("claude", "api-usage", "copilot"))
    assert sources["proxy"]["last_ok_run"] is not None  # the first day's run
    assert "stale_source proxy" in published["warnings"]
    fresh = json.loads((served / "exec" / "freshness.json").read_text(encoding="utf-8"))
    assert "proxy" in fresh["stale_sources"]
    run_day = (NOW + timedelta(days=1)).date().isoformat()
    assert fresh["data_through"] is not None and fresh["data_through"] < run_day


def test_the_provider_of_each_kind_is_what_its_connector_writes():
    import importlib

    from meterline_job.config import KINDS, PROVIDERS

    assert sorted(PROVIDERS) == sorted(KINDS)
    for kind, (module, _extra) in KINDS.items():
        assert PROVIDERS[kind] == importlib.import_module(module + ".normalise").PROVIDER, kind


# ---- v0.3 hardening A-5: one lock for silver, gold and publish --------------------------------------------------------


def _snapshot(folder):
    return {p.relative_to(folder).as_posix(): (p.stat().st_ino, p.stat().st_mtime_ns) for p in sorted(Path(folder).rglob("*"))}


def test_a_second_run_that_finds_the_gold_lock_held_leaves_silver_untouched(tmp_path, sim_org):
    from meterline.run import file_lock

    assert run(tmp_path, sim_org)[0] == 0
    silver = tmp_path / "data" / "silver"
    before = _snapshot(silver)
    assert before, "the first run staged no silver"
    builds = sorted(p.name for p in (tmp_path / "gold" / "builds").iterdir())
    with file_lock(tmp_path / "gold" / ".lock", "gold folder") as held:
        assert held is True
        code, lines = run(tmp_path, sim_org, days=1, no_retry=True)
    assert code == 1, lines
    for name in ("silver", "gold", "publish"):
        line = step(lines, name)
        assert (line["exit_code"], line["failure_kind"]) == (1, "transient"), line
        assert "in use by another run" in line["error"]
    assert _snapshot(silver) == before
    assert sorted(p.name for p in (tmp_path / "gold" / "builds").iterdir()) == builds


# ---- v0.3 hardening A-9: the lines of gold and publish say their own cause ---------------------------------------------


def test_a_gold_input_error_makes_publish_skipped_with_gold_s_code(tmp_path, sim_org, monkeypatch):
    monkeypatch.setattr(meterline_gold, "build", _GoldSpy(meterline_gold.InputError("prices.yaml: no such file")))
    code, lines = run(tmp_path, sim_org)
    gold, published = step(lines, "gold"), step(lines, "publish")
    assert (gold["exit_code"], gold["failure_kind"]) == (2, "input")
    assert (published["exit_code"], published["failure_kind"]) == (2, "skipped"), published
    assert "gold" in published["error"] and "nothing was published" in published["error"]
    assert code == 2


# ---- v0.3 hardening A-10: stdout carries JSON only ---------------------------------------------------------------------


def test_every_line_on_stdout_is_one_json_object(tmp_path, sim_org, capsys):
    code = run_job(config(tmp_path, sim_org), transports=transports(), now=lambda: NOW, sleep=lambda _s: None)
    captured = capsys.readouterr()
    assert code == 0
    lines = captured.out.splitlines()
    assert [json.loads(line)["step"] for line in lines] == STEPS
    assert all(isinstance(json.loads(line), dict) for line in lines)
    assert "published" in captured.err  # what a connector says for a person went to stderr


# ---- v0.3 hardening A-11: the locks fail closed ------------------------------------------------------------------------


def test_a_gold_root_where_the_lock_cannot_be_made_exits_2_before_any_connector_runs(tmp_path, sim_org):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x\n", encoding="utf-8")
    cfg = config(tmp_path, sim_org)
    cfg["gold_root"] = str(blocker / "gold")
    out = io.StringIO()
    code = run_job(cfg, transports=transports(), now=lambda: NOW, sleep=lambda _s: None, out=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert code == 2 and [line["step"] for line in lines] == ["job"], lines
    assert (lines[0]["exit_code"], lines[0]["failure_kind"]) == (2, "input") and "lock" in lines[0]["error"]
    assert not (tmp_path / "data").exists(), "a connector ran"


def test_a_platform_without_fcntl_exits_2_before_any_step(tmp_path, sim_org, monkeypatch):
    import meterline.run

    monkeypatch.setattr(meterline.run, "fcntl", None)
    for step_name in (None, "claude", "gold"):
        out = io.StringIO()
        code = run_job(config(tmp_path, sim_org), transports=transports(), now=lambda: NOW, sleep=lambda _s: None, out=out,
                       step=step_name)
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        assert code == 2 and [line["step"] for line in lines] == ["job"], (step_name, lines)
        assert "fcntl" in lines[0]["error"] and "POSIX" in lines[0]["error"]
    assert not (tmp_path / "data").exists()


def test_the_job_says_it_is_posix_only():
    try:
        import tomllib
    except ImportError:  # Python 3.10
        tomllib = pytest.importorskip("tomli")

    root = Path(meterline_job.__file__).resolve().parents[2]
    meta = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "Operating System :: OS Independent" not in meta["classifiers"]
    assert "Operating System :: POSIX" in meta["classifiers"] and "Operating System :: MacOS" in meta["classifiers"]
    assert "POSIX" in (root / "README.md").read_text(encoding="utf-8")


# ---- v0.3 hardening A-8: the connector step line counts its records ------------------------------------------------------------


@pytest.mark.parametrize("records, exit_code, total, empty", [({"usage": 0, "activity": 0}, 0, 0, True), ({}, 0, 0, True),
                                                              ({"usage": 4, "activity": 0}, 0, 4, False),
                                                              ({}, 1, 0, False)])  # a failed run is not "empty"
def test_the_connector_step_line_has_records_total_and_empty_when_nothing_was_published(monkeypatch, records, exit_code, total, empty):
    from meterline.run import RunResult

    result = RunResult(exit_code=exit_code, records=records)  # failure_kind is derived from the exit code
    monkeypatch.setattr(job_module, "run_connector", lambda *a, **k: ("backfill", result))
    line, code = job_module.run_step({"name": "claude", "kind": "anthropic", "since": "2026-09-01"}, "unused", None, NOW, lambda _s: None,
                                     retries=0)
    assert code == exit_code and line["records_total"] == total
    assert line.get("empty") is (True if empty else None)
