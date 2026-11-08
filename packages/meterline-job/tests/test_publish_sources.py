"""A-1 of the v0.3 hardening spec: the published folder says which source is stale.

Synthetic builds (``publish_helpers.make_build``); the connectors' ``complete_through`` is a table (``DONE``) and the
steps of the run are given to the publish step through the context, as the job does."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from meterline_job import publish
from publish_helpers import config, make_build

NOW = datetime(2026, 5, 4, 6, 0, tzinfo=timezone.utc)
TODAY = date(2026, 5, 4)
DONE = {}
BOTH_OK = {"claude": (True, "none"), "proxy": (True, "none")}


@pytest.fixture(autouse=True)
def connectors(monkeypatch):
    DONE.clear()
    DONE.update(claude=date(2026, 5, 2), proxy=date(2026, 5, 2))

    def complete_through(spec, data_dir):
        value = DONE[spec["name"]]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(publish, "connector_complete_through", complete_through)
    monkeypatch.setenv("METERLINE_GOLD_KEY", "a-key-of-at-least-32-bytes-that-you-keep-out-of-git")


def run(tmp_path, steps=BOTH_OK, **settings):
    make_build(tmp_path / "gold", "2026-05-04")
    cfg = config(tmp_path, publish=settings) if settings else config(tmp_path)
    ctx = publish.Context(today=TODAY, now=lambda: NOW, run_complete=False, source_steps=dict(steps) if steps is not None else {})
    return publish.run_publish(cfg, ctx)


def served(tmp_path, *parts):
    return json.loads((tmp_path / "gold" / "published" / Path(*parts)).read_text(encoding="utf-8"))


def source(tmp_path, name):
    return {s["name"]: s for s in served(tmp_path, "central", "published.json")["sources"]}[name]


def test_every_configured_source_has_an_entry_with_the_six_fields(tmp_path):
    assert run(tmp_path).exit_code == 0
    sources = served(tmp_path, "central", "published.json")["sources"]
    assert [s["name"] for s in sources] == ["claude", "proxy"]
    assert [s["provider"] for s in sources] == ["anthropic", "litellm"]
    for entry in sources:
        assert sorted(entry) == ["complete_through", "failure_kind", "last_ok_run", "name", "provider", "step_ok"]
    assert source(tmp_path, "claude")["complete_through"] == "2026-05-02"
    assert (source(tmp_path, "claude")["step_ok"], source(tmp_path, "claude")["failure_kind"]) == (True, "none")


def test_a_failed_step_makes_the_source_stale_with_a_warning(tmp_path):
    result = run(tmp_path, steps={"claude": (True, "none"), "proxy": (False, "transient")})
    assert result.exit_code == 0
    proxy = source(tmp_path, "proxy")
    assert (proxy["step_ok"], proxy["failure_kind"]) == (False, "transient")
    warnings = served(tmp_path, "central", "published.json")["warnings"]
    assert "stale_source proxy" in warnings and "stale_source claude" not in warnings
    assert result.extra["warnings"] == warnings
    for audience in (("exec",), ("manager", "mk_a"), ("person", "pk_a")):
        assert served(tmp_path, *audience, "freshness.json")["stale_sources"] == ["proxy"], audience


def test_a_source_older_than_the_setting_is_stale_and_one_on_the_limit_is_not(tmp_path):
    DONE["proxy"] = date(2026, 5, 1)  # three days before the run's day
    run(tmp_path)
    assert "stale_source proxy" not in served(tmp_path, "central", "published.json")["warnings"]
    DONE["proxy"] = date(2026, 4, 30)  # four days
    run(tmp_path)
    assert "stale_source proxy" in served(tmp_path, "central", "published.json")["warnings"]
    run(tmp_path, stale_after_days=4)
    assert "stale_source proxy" not in served(tmp_path, "central", "published.json")["warnings"]


def test_a_step_that_did_not_run_in_this_invocation_has_a_null_step(tmp_path):
    run(tmp_path, steps=None)
    for name in ("claude", "proxy"):
        assert (source(tmp_path, name)["step_ok"], source(tmp_path, name)["failure_kind"]) == (None, None)
    assert served(tmp_path, "exec", "freshness.json")["stale_sources"] == []


def test_freshness_files_carry_the_earliest_complete_through(tmp_path):
    DONE["proxy"] = date(2026, 4, 28)
    run(tmp_path)
    for audience in (("exec",), ("manager", "mk_a"), ("person", "pk_a"), ("person", "pk_b")):
        data = served(tmp_path, *audience, "freshness.json")
        assert sorted(data) == ["data_through", "frozen_months", "published_at", "stale_sources"], audience
        assert data["data_through"] == "2026-04-28"
    assert not (tmp_path / "gold" / "published" / "central" / "freshness.json").exists()


def test_data_through_is_null_when_one_source_has_no_answer(tmp_path):
    DONE["proxy"] = None
    run(tmp_path)
    assert served(tmp_path, "exec", "freshness.json")["data_through"] is None
    assert source(tmp_path, "proxy")["complete_through"] is None
    assert not [w for w in served(tmp_path, "central", "published.json")["warnings"] if w.startswith("complete_through_unavailable")]


def test_a_complete_through_that_raises_adds_its_own_warning(tmp_path):
    DONE["proxy"] = ValueError("state.json is damaged")
    assert run(tmp_path).exit_code == 0
    warnings = served(tmp_path, "central", "published.json")["warnings"]
    assert "complete_through_unavailable proxy" in warnings
    assert source(tmp_path, "proxy")["complete_through"] is None
    assert served(tmp_path, "exec", "freshness.json")["data_through"] is None


def test_last_ok_run_is_the_newest_published_manifest_of_the_data_folder(tmp_path):
    runs = tmp_path / "data" / "claude" / "runs"
    runs.mkdir(parents=True)
    for name, status, pulled in (("a.json", "published", "2026-05-01T05:00:00+00:00"), ("b.json", "published", "2026-05-03T05:30:00+00:00"),
                                 ("c.json", "failed", "2026-05-04T05:00:00+00:00")):
        (runs / name).write_text(json.dumps({"status": status, "pulled_at": pulled, "command": "daily"}), encoding="utf-8")
    run(tmp_path)
    assert source(tmp_path, "claude")["last_ok_run"] == "2026-05-03T05:30:00+00:00"
    assert source(tmp_path, "proxy")["last_ok_run"] is None


def test_the_stale_setting_is_a_whole_number_and_defaults_to_3():
    from meterline_job.config import PUBLISH_DEFAULTS, parse_config

    assert PUBLISH_DEFAULTS["stale_after_days"] == 3
    base = ('data_root = "d"\ngold_root = "g"\n[gold]\norg = "o"\npolicy = "p"\nprices = "x"\n'
            '[[connector]]\nname = "c"\nkind = "anthropic"\nsince = "2026-01-01"\n')
    assert parse_config((base + "[publish]\nstale_after_days = 5\n").encode())["publish"]["stale_after_days"] == 5
    from meterline_job.config import ConfigError

    with pytest.raises(ConfigError):
        parse_config((base + '[publish]\nstale_after_days = "3"\n').encode())
