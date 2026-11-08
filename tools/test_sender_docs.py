"""R-4 of the integrate part 2 spec: the documents a sender requires.

Example 07 is a sender of email (an example, not part of meterline), so the first update trigger of the threat model fires. The
threat model gets a row for it and a note under T10; the privacy statement's lines on a sender that is not built and on network
destinations are brought up to date; the gold README's "No delivery" line points to example 07 as an adopter's sender. The three
files must mention example 07 (the spec's check); the other tests hold what the wording has to say. Wording itself is for review.

Run: python3 -m pytest -q tools/test_sender_docs.py
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THREAT = "docs/THREAT-MODEL.md"
PRIVACY = "docs/PRIVACY.md"
GOLD_README = "packages/meterline-gold/README.md"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def section(text, heading):
    """The text under a ``##`` heading, up to the next ``##`` heading."""
    match = re.search(r"^## %s\s*$" % re.escape(heading), text, re.M)
    assert match, heading
    rest = text[match.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def mentions_example_07(text):
    return bool(re.search(r"example 07|examples/07-person-messages", text, re.I))


def test_the_threat_model_the_privacy_statement_and_the_gold_readme_mention_example_07():
    for rel in (THREAT, PRIVACY, GOLD_README):
        assert mentions_example_07(read(rel)), rel


def test_negative_control_a_file_without_the_mention_is_caught():
    assert not mentions_example_07("`delivery/directory.csv` exists for a sender that is not built.")
    assert mentions_example_07("an adopter's sender, such as [example 07](../examples/07-person-messages/README.md)")


# ---- the threat model ----

def test_the_threat_model_records_that_the_first_update_trigger_fired_and_when():
    text = read(THREAT)
    head = text[:1800]
    assert "Amended 2026-11-05" in head and mentions_example_07(head)
    triggers = section(text, "Update triggers")
    assert "sender of email" in triggers and re.search(r"2026-11-05", triggers) and mentions_example_07(triggers)


def test_the_diagram_draws_the_sender_and_the_mail_service_and_no_longer_says_not_built():
    diagram = re.search(r"```mermaid\n(.*?)```", read(THREAT), re.S).group(1)
    assert re.search(r'P5\["P5 [^"]*example 07', diagram), "P5 is not the example 07 sender"
    assert re.search(r'E5\["E5 [^"]*(SES|mail service)', diagram), "no mail service element"
    assert "not built" not in diagram
    assert re.search(r"D7 [-.>|a-z ]*P5", diagram) and re.search(r"P5 [-.>|a-zA-Z0-9 ]*E5", diagram)


def test_the_trust_boundary_table_has_a_row_for_the_sender_and_what_holds_at_the_crossing():
    rows = [l for l in section(read(THREAT), "Trust boundaries").splitlines() if l.startswith("| TB12")]
    assert len(rows) == 1
    row = rows[0]
    for needle in ("example 07", "SES", "METERLINE_MAIL_CONFIRM", "--send", "CI", "boto3"):
        assert needle in row, needle


def test_the_stride_table_has_rows_for_the_sender_and_a_note_under_t10():
    rows = [l for l in section(read(THREAT), "STRIDE").splitlines() if l.startswith("| T")]
    by_id = {r.split("|")[1].strip(): r for r in rows}
    sender_rows = [r for i, r in by_id.items() if re.fullmatch(r"T(29|3\d)", i)]
    assert len(sender_rows) >= 3 and all(mentions_example_07(r) for r in sender_rows)
    joined = "\n".join(sender_rows).lower()
    assert "wrong recipient" in joined or "another person" in joined
    assert "ci" in joined and "--send" in joined
    assert ".eml" in joined
    assert mentions_example_07(by_id["T10"]) and "central side" in by_id["T10"]


def test_the_attack_tree_no_longer_says_the_sender_is_not_built():
    leaf = [l for l in section(read(THREAT), "Attack tree").splitlines() if "2.8" in l]
    assert len(leaf) == 1 and "not built" not in leaf[0] and mentions_example_07(leaf[0])


# ---- the privacy statement ----

def test_privacy_names_the_sender_and_the_new_network_destination():
    text = read(PRIVACY)
    transfers = section(text, "Transfers")
    assert mentions_example_07(transfers) and "SES" in transfers
    assert "only when an adopter turns sending on" in transfers
    assert "api.anthropic.com" in transfers, "the provider destinations are still listed"


def test_privacy_no_longer_says_a_sender_is_not_built_or_that_nothing_uses_delivery():
    text = read(PRIVACY)
    assert "which is not built" not in text
    assert "Nothing uses it yet" not in text
    assert "A future email sender changes this" not in text
    inventory = [l for l in text.splitlines() if "delivery/directory.csv" in l and l.startswith("| Gold")]
    assert inventory and mentions_example_07(inventory[0])
    retention = [l for l in text.splitlines() if l.startswith("| `delivery/`")]
    assert retention and mentions_example_07(retention[0])


def test_privacy_keeps_the_checklist_before_sending_is_switched_on():
    transfers = section(read(PRIVACY), "Transfers")
    for needle in ("processing agreement", "GDPR art. 28", "nLPD art. 9", "DPIA", "THREAT-MODEL.md"):
        assert needle in transfers, needle


# ---- the gold README ----

def test_gold_readme_points_no_delivery_to_example_07_as_an_adopters_sender_not_part_of_meterline():
    lines = [l for l in read(GOLD_README).splitlines() if l.startswith("- **No delivery.**")]
    assert len(lines) == 1
    assert mentions_example_07(lines[0]) and "not part of meterline" in lines[0]
