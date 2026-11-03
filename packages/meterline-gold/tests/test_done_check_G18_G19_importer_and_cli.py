"""G18 (offline LiteLLM importer) and G19 (CLI exit codes)."""

import shutil
from pathlib import Path

import pytest

from gold_helpers import FIXTURE, KEY, compare_with_control, fixture_inputs, forbidden_imports


def test_done_check_G18_importer_output_is_byte_identical(tmp_path):
    from meterline_gold import cli

    out = tmp_path / "prices.yaml"
    args = [
        "import-litellm-prices", "--file", str(FIXTURE / "litellm" / "sample.json"),
        "--keys", str(FIXTURE / "litellm" / "keys.txt"), "--as-of", "2026-10-01", "--out", str(out),
    ]
    assert cli.main(args) == 0
    compare_with_control(out, FIXTURE / "litellm" / "expected-prices.yaml", "G18 importer")


def test_done_check_G18_missing_key_exits_2(tmp_path):
    from meterline_gold import cli

    keys = tmp_path / "keys.txt"
    keys.write_text("vendor-q/model-q-one\nmodel-q-absent\n", encoding="utf-8")
    out = tmp_path / "prices.yaml"
    args = [
        "import-litellm-prices", "--file", str(FIXTURE / "litellm" / "sample.json"),
        "--keys", str(keys), "--as-of", "2026-10-01", "--out", str(out),
    ]
    assert cli.main(args) == 2
    assert not out.exists()


def test_done_check_G18_importer_imports_no_network_module():
    import meterline_gold

    source = (Path(meterline_gold.__file__).resolve().parent / "importers" / "litellm.py").read_text(encoding="utf-8")
    assert forbidden_imports(source) == []
    # negative control: planted imports are flagged
    assert forbidden_imports("import urllib.request\n" + source) == ["urllib.request"]
    assert forbidden_imports("from http import client\n" + source) == ["http"]
    assert forbidden_imports("import requests\n" + source) == ["requests"]
    assert forbidden_imports("import socket\n" + source) == ["socket"]


def _args(inp, out):
    return [
        "build", "--silver", str(inp["silver"]), "--org-files", str(inp["org"]), "--policy", str(inp["policy"]),
        "--prices", str(inp["prices"]), "--invoice", str(inp["invoice"]), "--out", str(out),
    ]


def test_done_check_G19_cli_exit_codes(tmp_path, monkeypatch, capsys):
    import meterline_gold
    from meterline_gold import cli, run

    inp = fixture_inputs()
    monkeypatch.setenv("METERLINE_GOLD_KEY", KEY.decode())
    out = tmp_path / "gold"
    assert cli.main(_args(inp, out)) == 0
    printed = capsys.readouterr().out
    assert str(out) in printed and "2026-05" in printed
    assert (out / "manifest.json").is_file()
    # the same call again: the output folder is not empty
    assert cli.main(_args(inp, out)) == 2
    # no key
    monkeypatch.delenv("METERLINE_GOLD_KEY")
    assert cli.main(_args(inp, tmp_path / "gold2")) == 2
    assert not (tmp_path / "gold2").exists()
    # an assertion that always returns a row: exit 1 and nothing left behind
    monkeypatch.setenv("METERLINE_GOLD_KEY", KEY.decode())
    monkeypatch.setattr(run, "ASSERTIONS", list(run.ASSERTIONS) + [("AXX", "SELECT 1 AS broken")])
    out3 = tmp_path / "gold3"
    assert cli.main(_args(inp, out3)) == 1
    assert not out3.exists()
    assert not Path(str(out3) + ".tmp").exists()
    assert meterline_gold.__version__ == "0.2.0"
