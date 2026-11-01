"""F-43: the example dashboard meets the accessibility checks the review named.

These read the page source: a main landmark, accessible names on both selects, table headers with scope and a
caption per table, a polite live region for tab changes, focusable scroll regions, a selected tab that stays
visible in forced-colours mode, and the non-text contrast of tab borders and chart bars (3:1, WCAG 1.4.11) and
the text contrast (4.5:1) in both colour schemes. The full axe-core run in a browser is in the pull request; no
browser runs in this test suite.
"""

import re
from pathlib import Path

import meterline_gold

PAGE = Path(meterline_gold.__file__).resolve().parent / "dashboard" / "index.html"


def _text():
    return PAGE.read_text(encoding="utf-8")


def _script():
    return re.search(r"<script>(.*?)</script>", _text(), re.S).group(1)


def _style():
    return re.search(r"<style>(.*?)</style>", _text(), re.S).group(1)


def _schemes():
    """{scheme: {variable: hex}} from the :root blocks of the style (light first, dark in the media query)."""
    style = _style()
    dark_at = style.index("prefers-color-scheme: dark")
    out = {}
    for scheme, part in (("light", style[:dark_at]), ("dark", style[dark_at:])):
        block = re.search(r":root\s*{([^}]*)}", part).group(1)
        out[scheme] = dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", block))
    return out


def _luminance(hex_colour):
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_page_has_a_main_landmark_and_a_banner():
    text = _text()
    assert re.search(r"<main\b", text) and "</main>" in text
    assert re.search(r"<header\b", text)
    body = text.split("<body>", 1)[1].split("<script>", 1)[0]
    outside = re.sub(r"<header\b.*?</header>|<main\b.*?</main>", "", body, flags=re.S)
    assert not re.search(r"<(h1|h2|p|div|table)\b", outside), "content outside a landmark"


def test_both_selects_have_an_accessible_name():
    script = _script()
    names = re.findall(r"(col|lab)\.setAttribute\(\"aria-label\", ", script)
    assert sorted(names) == ["col", "lab"]


def test_tables_have_column_headers_with_scope_and_a_caption():
    script = _script()
    assert 'setAttribute("scope", "col")' in script
    assert 'el("caption"' in script
    assert 'el("thead")' in script and 'el("tbody")' in script


def test_a_polite_live_region_announces_the_file_shown():
    text = _text()
    region = re.search(r'<p[^>]*\baria-live="polite"[^>]*>', text)
    assert region and 'id="status"' in region.group(0)
    assert re.search(r'getElementById\("status"\)\.textContent = ', _script())


def test_scroll_regions_are_focusable_and_named():
    script = _script()
    assert 'wrap.setAttribute("tabindex", "0")' in script
    assert 'wrap.setAttribute("role", "region")' in script
    assert 'wrap.setAttribute("aria-label", ' in script


def test_selected_tab_stays_visible_in_forced_colours():
    style = _style()
    block = re.search(r"@media \(forced-colors: active\)\s*{(.*?)}\s*}", style, re.S)
    assert block and 'aria-pressed="true"' in block.group(1)


def test_tab_borders_and_chart_bars_reach_3_to_1_in_both_schemes():
    for scheme, var in _schemes().items():
        assert _contrast(var["control"], var["bg"]) >= 3.0, (scheme, "tab border", var["control"])
        assert _contrast(var["bar"], var["bg"]) >= 3.0, (scheme, "chart bar", var["bar"])
    assert re.search(r"\.tabs button\s*{[^}]*border: 1px solid var\(--control\)", _style())


def test_text_colours_reach_4_5_to_1_in_both_schemes():
    for scheme, var in _schemes().items():
        for name in ("fg", "muted", "accent", "warn"):
            assert _contrast(var[name], var["bg"]) >= 4.5, (scheme, name)
        # the selected tab: background text on an accent fill
        assert _contrast(var["bg"], var["accent"]) >= 4.5, (scheme, "selected tab")
