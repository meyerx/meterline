"""MF-10: the Admin key and the gold key are covered by the security documents, and the Admin README says what the
Admin key can do and lists exactly the requests the connector sends.

These are structure checks over the text: they fail when a part goes missing or when the README lists an endpoint the
code does not call (or leaves out one it does). They cannot judge whether a sentence is true; a reviewer does that.

Run: python3 -m pytest -q tools/test_security_docs.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import packages  # noqa: E402
from wiring import BY_ID  # noqa: E402  the places a package must be named in, shared with tools/check_new_connector.py

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN = os.path.join(ROOT, "packages", "meterline-anthropic-admin")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def section(text, heading):
    match = re.search(r"^###? %s\b.*?$(.*?)(?=^###? |\Z)" % re.escape(heading), text, re.M | re.S)
    assert match, "no section '%s'" % heading
    return match.group(1)


def table_lines(body):
    return [line for line in body.splitlines() if line.startswith("|")]


def code_endpoints():
    """The paths the Admin connector requests, read from its client."""
    client = read("packages", "meterline-anthropic-admin", "src", "meterline_anthropic_admin", "client.py")
    paths = re.findall(r'^[A-Z_]+_PATH = "(/v1/[^"]+)"', client, re.M)
    assert len(paths) == 2, paths
    return paths


# ---- the Admin README ----

def test_admin_readme_lists_exactly_the_requests_the_code_sends():
    body = section(read("packages", "meterline-anthropic-admin", "README.md"), "What the Admin key can do")
    listed = re.findall(r"`GET (/v1/[^`?]+)", body)
    assert sorted(listed) == sorted(code_endpoints()), (listed, code_endpoints())
    assert not re.findall(r"`(?:POST|PUT|PATCH|DELETE) /", body), "the list names a request that is not a GET"


def test_admin_readme_says_what_the_key_can_do_and_who_makes_it():
    text = read("packages", "meterline-anthropic-admin", "README.md")
    body = section(text, "What the Admin key can do")
    low = body.lower()
    for words in ("members", "invites", "workspaces", "api keys"):
        assert words in low, words
    assert "no scopes" in low and "workspace" in low and "does not work" in low
    assert "admin role" in low
    assert "dedicated" in low and "expir" in low and "reuse" in low
    assert "rotat" in low and "revoke" in low


def test_admin_readme_says_the_key_is_not_read_only():
    body = section(read("packages", "meterline-anthropic-admin", "README.md"), "What the Admin key can do")
    assert "not a read-only key" in body


# ---- AGENTS.md and the front doors ----

def test_no_front_door_calls_every_provider_key_read_only():
    for name in ("AGENTS.md", "README.md"):
        text = read(name)
        assert "need a read-only key" not in text, name
        assert "create a read-only key or token" not in text, name
    assert "create a read-only key" not in read("docs", "README.md")


# ---- SECURITY.md ----

def test_security_policy_names_both_keys_and_every_package():
    text = read("SECURITY.md")
    assert "ANTHROPIC_ADMIN_KEY" in text and "METERLINE_GOLD_KEY" in text
    first = text.split("## ", 1)[0]
    assert "Admin" in first, "the opening paragraph leaves out the Admin key"
    table = section(text, "Supported versions")
    for package in packages():
        assert "`%s`" % package in table, package
        assert BY_ID["security-md"].missing(ROOT, package) == [], package  # also the keys of a connector in the Scope
    scope = section(text, "Scope")
    assert "ANTHROPIC_ADMIN_KEY" in scope and "METERLINE_GOLD_KEY" in scope


def test_security_policy_says_how_the_gold_key_is_made_rotated_and_what_rotation_does():
    body = section(read("SECURITY.md"), "The gold hashing key")
    low = body.lower()
    assert "32 bytes" in low and "token_urlsafe" in body
    assert "rotat" in low and "person" in low and "folder" in low
    assert "does not generate" in low or "never generates" in low


# ---- THREAT-MODEL.md ----

def test_threat_model_covers_the_admin_connector_and_both_keys():
    text = read("docs", "THREAT-MODEL.md")
    assert "anthropic-admin" in text
    assets = section(text, "What is worth protecting")
    assert "ANTHROPIC_ADMIN_KEY" in assets and "METERLINE_GOLD_KEY" in assets
    assert "api.anthropic.com" in assets or "api.anthropic.com" in text
    boundaries = "\n".join(table_lines(section(text, "Trust boundaries")))
    assert "ANTHROPIC_ADMIN_KEY" in boundaries, "no trust-boundary row for the Admin key"
    assert "METERLINE_GOLD_KEY" in boundaries, "no trust-boundary row for the gold key"
    assert "calibrate_anthropic_admin.py" in text


def test_threat_model_has_stride_rows_for_a_stolen_admin_key_and_a_hostile_report():
    rows = [r for r in table_lines(section(read("docs", "THREAT-MODEL.md"), "STRIDE")) if r.startswith("| T")]
    rows = [row.lower() for row in rows]
    joined = "\n".join(rows)
    assert any(re.search(r"stolen (anthropic )?admin key", row) for row in rows)
    assert any("hostile" in row and "admin report" in row for row in rows)
    assert "calibrate_anthropic_admin.py" in joined


# ---- the claim "GET only" has a control ----

NOT_GET = re.compile(r"\.(post|put|patch|delete|request|send|stream)\(")


def non_get_calls(source):
    return NOT_GET.findall(source)


def test_the_scan_for_non_get_calls_sees_a_planted_post():
    assert non_get_calls("response = self._http.post(endpoint, json=body)") == ["post"]
    assert non_get_calls("response = self._http.get(endpoint, params=params)") == []


def test_admin_connector_source_sends_no_request_but_get():
    folder = os.path.join(ADMIN, "src", "meterline_anthropic_admin")
    for name in sorted(os.listdir(folder)):
        if name.endswith(".py"):
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                assert non_get_calls(fh.read()) == [], name
