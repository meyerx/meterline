"""Empty input: a silver folder with no records, or an org folder that does not exist, is an input error (exit 2).

The usual mistake is pointing --silver one level too deep, at <silver>/records instead of <silver>. Every case
checks the exit code, that stderr names the offending folder, and that neither --out nor --out.tmp was created.
"""

import shutil

import pytest

from gold_helpers import fixture_inputs


@pytest.fixture
def cli_env(monkeypatch):
    monkeypatch.setenv("METERLINE_GOLD_KEY", "meterline-gold-fixture-key-not-a-secret")


def _run(cli_env, tmp_path, silver=None, org=None, capsys=None):
    from meterline_gold import cli

    inp = fixture_inputs()
    out = tmp_path / "gold"
    code = cli.main([
        "build", "--silver", str(silver if silver is not None else inp["silver"]),
        "--org-files", str(org if org is not None else inp["org"]),
        "--policy", str(inp["policy"]), "--prices", str(inp["prices"]), "--out", str(out),
    ])
    err = capsys.readouterr().err
    return code, err, out


def _assert_nothing_written(out):
    assert not out.exists()
    assert not out.with_name(out.name + ".tmp").exists()


def test_silver_one_level_too_deep(cli_env, tmp_path, capsys):
    deep = tmp_path / "records"
    shutil.copytree(fixture_inputs()["silver"] / "records", deep)
    code, err, out = _run(cli_env, tmp_path, silver=deep, capsys=capsys)
    assert code == 2
    assert str(deep) in err
    assert "folder above records/" in err
    _assert_nothing_written(out)


@pytest.mark.parametrize("kinds", [False, True], ids=["records-empty", "empty-kind-folders"])
def test_silver_records_folder_holds_no_files(cli_env, tmp_path, capsys, kinds):
    from meterline import KINDS

    silver = tmp_path / "silver"
    (silver / "records").mkdir(parents=True)
    if kinds:
        for kind in KINDS:
            (silver / "records" / kind).mkdir()
    code, err, out = _run(cli_env, tmp_path, silver=silver, capsys=capsys)
    assert code == 2
    assert str(silver) in err
    assert "folder above records/" in err
    _assert_nothing_written(out)


def test_silver_files_present_but_no_records(cli_env, tmp_path, capsys):
    """Files exist but hold no record, so no month can be derived: still an input error, nothing written."""
    from meterline import KINDS

    silver = tmp_path / "silver"
    for kind in KINDS:
        (silver / "records" / kind).mkdir(parents=True)
        (silver / "records" / kind / "empty.jsonl").write_text("", encoding="utf-8")
    code, err, out = _run(cli_env, tmp_path, silver=silver, capsys=capsys)
    assert code == 2
    assert str(silver) in err
    assert "folder above records/" in err
    _assert_nothing_written(out)


def test_org_folder_missing(cli_env, tmp_path, capsys):
    org = tmp_path / "no-such-org"
    code, err, out = _run(cli_env, tmp_path, org=org, capsys=capsys)
    assert code == 2
    assert str(org) in err
    _assert_nothing_written(out)
