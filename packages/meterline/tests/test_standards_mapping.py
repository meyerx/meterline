"""Review finding F-44: docs/standards-mapping.md maps every record field to FOCUS and OpenTelemetry, or says it has
no equivalent, and names the versions it was checked against, the date and a review-by date."""

import re
from dataclasses import fields
from datetime import date
from pathlib import Path

import pytest

import meterline as ml

ROOT = Path(__file__).resolve().parents[3]
MAPPING = ROOT / "docs" / "standards-mapping.md"


def _text():
    return MAPPING.read_text(encoding="utf-8")


def _section(heading):
    text = _text()
    start = text.index("\n" + heading + "\n")
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else len(text)]


def _mapped_fields(section):
    """Field names in the first column of the section's table rows; each row fills the FOCUS and OpenTelemetry cells."""
    names = set()
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 4 and all(cells[:3]), line
        names.update(re.findall(r"`(\w+)`", cells[0]))
    return names


@pytest.mark.parametrize("kind", sorted(ml.KINDS))
def test_every_field_of_every_record_type_has_a_row(kind):
    cls = ml.KINDS[kind]
    mapped = _mapped_fields(_section("## " + cls.__name__))
    missing = sorted({f.name for f in fields(cls)} - mapped)
    assert missing == []


def test_the_standards_are_cited_with_version_status_url_and_date():
    section = _section("## Standards checked")
    assert "FOCUS" in section and "1.4" in section and "https://focus.finops.org/docs/specification/v1-4/" in section
    assert "OpenTelemetry" in section and "https://github.com/open-telemetry/semantic-conventions-genai" in section
    assert re.search(r"\b[0-9a-f]{40}\b", section), "the OpenTelemetry commit checked"
    assert "Development" in section
    assert "2026-10-17" in section


def test_a_review_by_date_is_set_and_in_the_future_of_the_check():
    found = re.search(r"\*\*Review by:\*\* (\d{4}-\d{2}-\d{2})", _text())
    assert found and date.fromisoformat(found.group(1)) > date(2026, 10, 17)


def test_the_cost_decision_is_recorded():
    section = _section("## Decisions")
    assert "is not `EffectiveCost`" in section
    assert "`estimate`" in section and "`consumption`" in section
    for needed in ("seat", "Allocated", "cache", "response.model"):
        assert needed in section, needed


def test_the_readme_points_at_the_mapping_and_not_at_the_moved_page():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/standards-mapping.md" in readme
    assert "https://opentelemetry.io/docs/specs/semconv/gen-ai/" not in readme
