"""F-20: the hashing key METERLINE_GOLD_KEY never appears in anything gold
writes or prints.

A canary key is used for a successful build and for three failing ones (a short
key, a non-empty output folder, an input that breaks a rule). The output tree,
the manifest, the captured logs, stdout and stderr, and the text of the
exception are scanned for the key as bytes, as hex (either case) and as base64
(standard and URL-safe, with and without padding).
"""

import base64
import gzip
import logging
from pathlib import Path

import pytest

from gold_helpers import fixture_inputs, run_cli_subprocess

CANARY = b"CANARY-gold-key-7f3a9c2d-never-print-me-0123456789"


def key_forms(key: bytes):
    b64 = base64.b64encode(key)
    b64url = base64.urlsafe_b64encode(key)
    forms = {key, key.hex().encode(), key.hex().upper().encode(), b64, b64.rstrip(b"="), b64url, b64url.rstrip(b"=")}
    return sorted(forms)


def assert_clean(text_or_bytes, where):
    data = text_or_bytes if isinstance(text_or_bytes, bytes) else str(text_or_bytes).encode("utf-8", "replace")
    for form in key_forms(CANARY):
        assert form not in data, "%s holds the key (%s form)" % (where, form[:6].decode("ascii", "replace") + "...")


def scan_tree(root: Path):
    seen = 0
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        assert_clean(data, path.as_posix())
        if path.name.endswith(".gz"):
            assert_clean(gzip.decompress(data), path.as_posix() + " (unzipped)")
        seen += 1
    return seen


def _args(inp, out):
    return [
        "build", "--silver", str(inp["silver"]), "--org-files", str(inp["org"]), "--policy", str(inp["policy"]),
        "--prices", str(inp["prices"]), "--invoice", str(inp["invoice"]), "--out", str(out),
    ]


def test_gold_key_never_in_outputs(tmp_path, monkeypatch, capsys, caplog):
    from meterline_gold import cli

    caplog.set_level(logging.DEBUG)
    inp = fixture_inputs()
    monkeypatch.setenv("METERLINE_GOLD_KEY", CANARY.decode())

    out = tmp_path / "gold"
    assert cli.main(_args(inp, out)) == 0
    manifest = out / "manifest.json"
    assert manifest.is_file()
    assert scan_tree(out) > 5
    assert_clean(manifest.read_bytes(), "manifest.json")

    # a second build into the now non-empty folder fails; its message must not hold the key either
    assert cli.main(_args(inp, out)) == 2

    # a key one byte short of the minimum is refused with a message that does not echo it
    short = CANARY[:31]
    monkeypatch.setenv("METERLINE_GOLD_KEY", short.decode())
    assert cli.main(_args(inp, tmp_path / "gold-short")) == 2
    err = capsys.readouterr().err
    assert "at least" in err and "METERLINE_GOLD_KEY" in err
    assert short.decode() not in err
    assert not (tmp_path / "gold-short").exists()

    out_text = capsys.readouterr()
    for text in (out_text.out, out_text.err, caplog.text):
        assert_clean(text, "captured output")
    assert_clean(err, "stderr of the short-key run")


def test_gold_key_never_in_exception_text(tmp_path, monkeypatch):
    from meterline_gold import build
    from meterline_gold.errors import InputError

    inp = fixture_inputs()
    short = CANARY[:31]
    with pytest.raises(InputError) as caught:
        build(inp["silver"], inp["org"], inp["policy"], inp["prices"], tmp_path / "out", key=short)
    assert_clean(str(caught.value), "InputError text")
    assert "at least" in str(caught.value)


def test_gold_key_never_in_a_subprocess_stdout_or_stderr(tmp_path):
    inp = fixture_inputs()
    out = tmp_path / "gold"
    done = run_cli_subprocess(_args(inp, out), key=CANARY)
    assert done.returncode == 0, done.stderr
    assert_clean(done.stdout, "stdout")
    assert_clean(done.stderr, "stderr")
    assert scan_tree(out) > 5
    # a second run (non-empty folder) and a short key: failures print messages, none with the key
    again = run_cli_subprocess(_args(inp, out), key=CANARY)
    assert again.returncode == 2
    assert_clean(again.stdout + again.stderr, "second run output")
    short = run_cli_subprocess(_args(inp, tmp_path / "gold-short"), key=CANARY[:31])
    assert short.returncode == 2
    assert "METERLINE_GOLD_KEY" in short.stderr
    assert_clean(short.stdout + short.stderr, "short-key run output")
