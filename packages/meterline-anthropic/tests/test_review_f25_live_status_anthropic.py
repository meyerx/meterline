"""F-25: the command line says the connector is not yet verified against a live account.

The README is not the only place a user meets the connector: `--help` says it too, in the words of the
evidence levels (docs/glossary.md).
"""

import contextlib

from meterline_anthropic.cli import main


def test_help_says_the_shapes_are_unverified_until_a_live_run(capsys):
    with contextlib.suppress(SystemExit):
        main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "built against documented shapes" in out
    assert "not yet verified against a live" in out
    assert "Response shapes" in out
