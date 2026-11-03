"""``check`` and the configuration: what is refused before anything runs, and the command line."""
import json
import shutil
from pathlib import Path

import pytest

from meterline_job import cli
from meterline_job.config import ConfigError, parse_config

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples" / "03-daily-job" / "job.example.toml"

GOOD = """
data_root = "job-data"
gold_root = "job-data/gold"

[gold]
org = "org"
policy = "policy.yaml"
prices = "prices.yaml"

[[connector]]
name = "claude"
kind = "anthropic"
since = "2026-09-01"
"""


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """A folder holding the files the example configuration names, as the job finds them when run from there."""
    (tmp_path / "org").mkdir()
    (tmp_path / "policy.yaml").write_text("x: 1\n", encoding="utf-8")
    (tmp_path / "prices.yaml").write_text("x: 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write(workdir, text):
    path = workdir / "job.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def check(workdir, text, capsys):
    code = cli.main(["check", write(workdir, text)])
    return code, capsys.readouterr()


def test_example_toml_passes_check(workdir, capsys):
    shutil.copy(EXAMPLE, workdir / "job.toml")
    assert cli.main(["check", str(workdir / "job.toml")]) == 0, capsys.readouterr()


def test_a_good_file_passes(workdir, capsys):
    assert check(workdir, GOOD, capsys)[0] == 0


@pytest.mark.parametrize("text, words", [
    ('stray = 1\n' + GOOD, "stray"),                                                        # a top-level key
    (GOOD.replace('prices = "prices.yaml"', 'prices = "prices.yaml"\nparqet = true'), "parqet"),  # a typo under [gold]
    (GOOD.replace('since = "2026-09-01"', ""), "since"),                                    # a connector with no since
    (GOOD.replace('kind = "anthropic"', 'kind = "nosuch"'), "nosuch"),
    (GOOD.replace('policy = "policy.yaml"', 'policy = "missing.yaml"'), "missing.yaml"),    # a path the file names
    (GOOD.replace('prices = "prices.yaml"', 'prices = "prices.yaml"\nparquet = "yes"'), "parquet"),
    (GOOD.replace('prices = "prices.yaml"', 'prices = "prices.yaml"\ninvoice = "no-invoice.csv"'), "no-invoice.csv"),
])
def test_check_rejects_unknown_key(workdir, capsys, text, words):
    code, seen = check(workdir, text, capsys)
    assert code == 2 and words in seen.err, seen


def test_reserved_step_names_refused(workdir, capsys):
    for name in ("silver", "gold"):
        code, seen = check(workdir, GOOD.replace('name = "claude"', 'name = "%s"' % name), capsys)
        assert code == 2 and name in seen.err and "reserved" in seen.err, seen


def test_two_connectors_may_not_share_a_name(workdir, capsys):
    code, seen = check(workdir, GOOD + '\n[[connector]]\nname = "claude"\nkind = "copilot"\nsince = "2026-09-01"\n', capsys)
    assert code == 2 and "claude" in seen.err, seen


def test_a_missing_extra_is_an_input_error_naming_it(workdir, capsys, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "meterline_anthropic", None)
    code, seen = check(workdir, GOOD, capsys)
    assert code == 2 and "meterline-job[anthropic]" in seen.err, seen


def test_check_makes_no_network_call_and_writes_nothing(workdir, capsys):
    before = sorted(p.name for p in workdir.iterdir())
    assert check(workdir, GOOD, capsys)[0] == 0  # the network guard of packages/conftest.py fails a test that connects
    assert sorted(p.name for p in workdir.iterdir()) == sorted(before + ["job.toml"])


def test_parse_config_gives_the_defaults():
    config = parse_config(GOOD.encode("utf-8"))
    assert config["gold"]["parquet"] is True and config["gold"].get("invoice") is None
    with pytest.raises(ConfigError):
        parse_config(b"this is = not toml [")


def test_help_lists_both_commands_and_the_options(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["--help"])
    assert stop.value.code == 0
    text = capsys.readouterr().out
    for word in ("run", "check"):
        assert word in text
    with pytest.raises(SystemExit):
        cli.main(["run", "--help"])
    text = capsys.readouterr().out
    assert "--step" in text and "--no-retry" in text


def test_run_on_a_bad_file_exits_2_without_running(workdir, capsys):
    assert cli.main(["run", write(workdir, "stray = 1\n" + GOOD)]) == 2
    assert "stray" in capsys.readouterr().err


def test_the_silver_step_alone_through_the_command_line(workdir, capsys):
    """No connector, no network: `--step silver` stages what is on disk and prints one JSON line."""
    folder = workdir / "job-data" / "claude" / "records" / "usage"
    folder.mkdir(parents=True)
    (folder / "b1.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
    code = cli.main(["run", write(workdir, GOOD), "--step", "silver"])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert code == 0 and [x["step"] for x in lines] == ["silver"]
    assert (workdir / "job-data" / "silver" / "records" / "usage" / "claude-b1.jsonl").is_file()
