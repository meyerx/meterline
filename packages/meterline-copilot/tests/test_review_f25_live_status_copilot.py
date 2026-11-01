"""F-25: the command line says the connector is not yet verified against a live account.

The README is not the only place a user meets the connector: `--help` says it too, in the words of the
evidence levels (docs/glossary.md).
"""

import contextlib

from meterline_copilot.cli import main


def test_help_says_the_shapes_are_unverified_until_a_live_run(capsys):
    with contextlib.suppress(SystemExit):
        main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "built against documented shapes" in out
    assert "not yet verified against a live" in out
    assert "Response shapes" in out


def test_every_api_request_pins_the_github_api_version():
    """GitHub serves the REST API in dated versions; the shapes were transcribed from 2026-03-10."""
    import httpx

    from meterline_copilot.client import CopilotClient

    seen = []

    def handler(request):
        seen.append(dict(request.headers))
        return httpx.Response(200, json={"total_seats": 0, "seats": []})

    client = CopilotClient("token-for-tests-only", transport=httpx.MockTransport(handler))
    client.get_json("orgs/example-org/copilot/billing/seats")
    client.get_json("organizations/example-org/settings/billing/ai_credit/usage", billing=True)
    assert len(seen) == 2
    for headers in seen:
        assert headers["x-github-api-version"] == "2026-03-10"
        assert headers["accept"] == "application/vnd.github+json"
