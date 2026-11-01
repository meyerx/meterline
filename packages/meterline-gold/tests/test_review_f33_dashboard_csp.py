"""F-33: the example dashboard carries a strict content security policy.

The page stays one static file, so its one inline script and its one inline style are allowed by their sha256
hash, never by 'unsafe-inline'. Any edit of the script or the style changes its hash, and this test then fails
until the policy carries the new one: the policy cannot silently drift from the page.
"""

import base64
import hashlib
import re
from pathlib import Path

import meterline_gold

PAGE = Path(meterline_gold.__file__).resolve().parent / "dashboard" / "index.html"


def _text():
    return PAGE.read_text(encoding="utf-8")


def _policy(text):
    match = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', text)
    assert match, "the dashboard has no Content-Security-Policy meta tag"
    directives = {}
    for part in match.group(1).split(";"):
        words = part.split()
        if words:
            directives[words[0]] = words[1:]
    return match, directives


def _hash(body):
    return "'sha256-%s'" % base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode("ascii")


def test_dashboard_csp_is_strict():
    _, policy = _policy(_text())
    assert policy.get("default-src") == ["'none'"]
    assert policy.get("connect-src") == ["'self'"], "the page reads its CSV files from its own folder only"
    assert policy.get("base-uri") == ["'none'"]
    assert policy.get("form-action") == ["'none'"]
    for name in ("script-src", "style-src"):
        sources = policy.get(name, [])
        assert sources and all(s.startswith("'sha256-") for s in sources), (name, sources)


def test_dashboard_csp_comes_before_any_script_or_style():
    text = _text()
    match, _ = _policy(text)
    first = min(text.index("<script"), text.index("<style"))
    assert match.start() < first


def test_dashboard_csp_hashes_match_the_inline_script_and_style():
    text = _text()
    _, policy = _policy(text)
    scripts = re.findall(r"<script>(.*?)</script>", text, re.S)
    styles = re.findall(r"<style>(.*?)</style>", text, re.S)
    assert len(scripts) == 1 and len(styles) == 1
    assert policy["script-src"] == [_hash(scripts[0])]
    assert policy["style-src"] == [_hash(styles[0])]


def test_dashboard_csp_page_has_no_other_script_route():
    text = _text()
    assert not re.search(r"<script[^>]*\bsrc=", text)
    assert not re.search(r"\son[a-z]+\s*=", text), "an inline event handler would need 'unsafe-inline'"
    assert "javascript:" not in text
    assert not re.search(r"\.style\b|setAttribute\(\s*['\"]style", text), "a style attribute set by script"
