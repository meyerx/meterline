"""The command line says the connector was verified live, in the words of the README Status line.

The README is not the only place a user meets the connector: `--help` says it too, in the words of the
evidence levels (docs/glossary.md). The first live run passed on 2026-10-30.
"""

import contextlib

from meterline_anthropic_admin.cli import main


def test_help_says_the_connector_was_verified_live_on_a_small_organisation(capsys):
    with contextlib.suppress(SystemExit):
        main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "verified live" in out
    assert "small Console organisation" in out
    assert "not yet verified live" not in out
    assert "built against documented shapes" not in out
    assert "Response shapes" in out
