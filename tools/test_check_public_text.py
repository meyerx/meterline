"""tools/check_public_text.py runs the export's scan on a text before it is typed into a public page: PASS for the fixed
export-PR body, FAIL for a body that carries a backlog-shaped id.

Run: python3 -m pytest -q tools
"""
import os
import subprocess
import sys

import pytest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_public_text.py")
FIXED_BODY = "Export of export/v0.2. What changed: CHANGELOG.md.\n"


@pytest.fixture
def denylist(tmp_path):
    path = tmp_path / "terms.txt"
    path.write_text("no-such" "-term-in-any-text\n", encoding="utf-8")
    return str(path)


def run(tmp_path, denylist, body):
    text = tmp_path / "body.txt"
    text.write_text(body, encoding="utf-8")
    return subprocess.run([sys.executable, TOOL, str(text), "--denylist", denylist], capture_output=True, text=True)


def test_the_fixed_export_body_passes(tmp_path, denylist):
    result = run(tmp_path, denylist, FIXED_BODY)
    assert (result.returncode, result.stdout.strip()) == (0, "PASS"), result.stderr


def test_a_body_with_a_backlog_shaped_id_fails(tmp_path, denylist):
    result = run(tmp_path, denylist, FIXED_BODY + "Closes " + "METR" + "-12.\n")
    assert result.returncode == 1 and result.stdout.startswith("FAIL") and "backlog ticket id" in result.stdout
    assert "METR" not in result.stdout  # the hit is named by kind, never printed


def test_a_body_with_a_denylist_term_fails_without_printing_it(tmp_path):
    terms = tmp_path / "terms.txt"
    terms.write_text("re:acme" "corp\n", encoding="utf-8")
    result = run(tmp_path, str(terms), "Needed for AcmeCorp.\n")
    assert result.returncode == 1 and "denylist term" in result.stdout and "acme" not in result.stdout.lower()
