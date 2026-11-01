"""B3-b: the privacy statement and the places that list its exceptions say what the code does.

These are text checks, with two probes of the code (what `servable` returns, what the unknown-field report names). They
fail when a statement goes missing or an old one returns. They cannot judge whether a sentence is true; a reviewer
does that against the code.

Run: python3 -m pytest -q tools/test_b3b_privacy_pass.py
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def section(text, heading):
    match = re.search(r"^#{2,3} %s\b.*?$(.*?)(?=^#{2,3} |\Z)" % re.escape(heading), text, re.M | re.S)
    assert match, "no section '%s'" % heading
    return match.group(1)


PRIVACY = read("docs", "PRIVACY.md")
README = read("README.md")
THREAT = read("docs", "THREAT-MODEL.md")
GOLD_README = read("packages", "meterline-gold", "README.md")


def test_privacy_names_the_admin_connector_and_sorts_its_identifiers():
    assert "anthropic-admin" in PRIVACY
    for name in ("api_key_id", "workspace_id", "account_id", "service_account_id", "description"):
        assert "`%s`" % name in PRIVACY, name
    admin = section(PRIVACY, "The Anthropic Admin connector")
    assert "`actor_id`" in admin and "raw" in admin


def test_privacy_gives_the_label_lengths_the_code_writes():
    assert "12-character" not in PRIVACY
    assert "16 hexadecimal" in PRIVACY and "12 hexadecimal" in PRIVACY
    inventory = section(PRIVACY, "Personal-data inventory")
    assert "`windows`" in inventory and "`token_anomalies`" in inventory and "`feature_sum_mismatch`" in inventory
    erasure = section(PRIVACY, "Erasure")
    assert "`windows`" in erasure, "the erasure steps do not cover the labels in runs/*.json windows"


def test_privacy_says_how_gold_really_matches_a_person():
    why = section(PRIVACY, "Why `actor_email` is kept in raw and silver")
    assert "matches a provider account to a person by email" not in why
    assert "keyed hash" in why and "`actor_id`" in why
    assert "leak check" in why


def test_privacy_covers_the_second_copy_of_silver_that_example_03_makes():
    assert "stage_silver" in PRIVACY
    erasure = section(PRIVACY, "Erasure")
    assert "<data_root>/silver" in erasure
    assert "<data_root>/silver" in section(PRIVACY, "Retention")


def test_every_never_add_bullet_has_a_test_or_says_no_test_yet():
    bullets = [b for b in section(PRIVACY, "Fields that must never be added").splitlines() if b.startswith("- ")]
    assert len(bullets) == 7
    no_test = [b for b in bullets if "no test yet" in b]
    assert len(no_test) == 3, no_test
    for b in bullets:
        assert ("no test yet" in b) != bool(re.search(r"\btests?\b `", b)), b


def test_the_gold_readme_sentence_on_servable_matches_what_servable_returns():
    pytest.importorskip("meterline_gold")
    from meterline_gold import servable

    assert servable("manifest.json", "central") is True
    assert all(servable("manifest.json", who) is False for who in ("exec", "manager", "person"))
    assert all(servable("delivery/directory.csv", who) is False for who in ("central", "exec", "manager", "person"))
    paragraph = next(p for p in GOLD_README.split("\n\n") if "meterline_gold.servable(path, audience)" in p)
    assert "`manifest.json` when the audience is `central`" in paragraph, paragraph
    assert "`delivery/` and `manifest.json` whatever the audience" not in paragraph, paragraph


def test_readme_and_threat_model_list_the_derivable_seat_count():
    for name, text in (("README.md", README), ("THREAT-MODEL.md", THREAT)):
        assert "`active_seats`" in text and "`idle_seats`" in text, name


def test_readme_and_threat_model_list_the_two_error_echoes():
    security = section(README, "Security and data handling")
    assert "never email addresses" not in security
    assert "unknown-field report" in security and "identity-overlap" in security
    assert "shape error" not in security
    v16 = next(line for line in THREAT.splitlines() if line.startswith("| V16"))
    assert "unknown-field report" in v16 and "identity-overlap" in v16
    assert "shape error" not in v16
    assert "never an email" not in v16


def test_an_unknown_field_name_is_echoed_in_the_unknown_fields_report(tmp_path, capsys):
    """The probe the exception rests on: an unknown field named like an email is in the manifest path and the warning, its value is not."""
    pytest.importorskip("meterline_anthropic_admin")
    import json
    from datetime import date, datetime, timezone

    from meterline_anthropic_admin import run
    from meterline_anthropic_admin.testing import FakeAPI

    name, value, key = "victim.person@example.com", "value-that-must-never-be-printed", "sk" + "-ant-admin01-synthetic-not-a-real-key"
    api = FakeAPI(api_key=key)
    api.extra_usage_fields[name] = value
    result = run("backfill", since=date(2026, 9, 1), provider_account_id="org-synthetic", data_dir=tmp_path, credentials={"ANTHROPIC_ADMIN_KEY": key},
                 transport=api.transport, now=lambda: datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc))
    assert result.exit_code == 0
    err = capsys.readouterr().err
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["failures"] == [], "the report is not a failure"
    paths = [item["path"] for item in manifest["unknown_fields"]]
    assert paths == ["data[].results[]." + name]
    assert name in err and value not in err and value not in json.dumps(manifest)
