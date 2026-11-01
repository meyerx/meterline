"""Integration base B4: the audience rules in machine form, and ``servable(path, audience)``.

- for every file of a ``tiny`` build (seed 7), ``servable`` agrees with the table "What a build writes" in the gold
  README; ``delivery/directory.csv`` and ``manifest.json`` are never servable to exec, manager or person;
- ``audiences.json`` is shipped with the package, committed to ``docs/schemas/gold-audiences.json`` with its own
  schema, and says the truth about the files (person keys, hidden marker);
- nothing is added to the gold folder.

Synthetic data only.
"""

import csv
import json
import re
from pathlib import Path

import pytest

from gold_helpers import KEY

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "docs" / "schemas"
README = Path(__file__).resolve().parents[1] / "README.md"
REGENERATE = "regenerate with: python -m meterline_gold.contract docs/schemas docs/reference/gold-datasets.md"
READERS = ("central", "exec", "manager", "person")

# What the README table says about each row label, written out by hand: who may be served that folder or file.
README_SERVES = {
    "central/": {"central"},
    "manager/<manager_key>/": {"manager"},
    "person/<person_key>/": {"person"},
    "exec/": {"exec"},
    "delivery/": set(),            # "Never serve it."
    "manifest.json": {"central"},  # "it belongs with central/: never deliver it to an audience"
}


@pytest.fixture(scope="module")
def tiny_gold(tmp_path_factory):
    import meterline_gold
    meterline_sim = pytest.importorskip("meterline_sim")

    run = meterline_sim.generate(preset="tiny", seed=7, months=6, out=tmp_path_factory.mktemp("b4_sim"))
    base = Path(meterline_gold.__file__).resolve().parent / "examples"
    out = tmp_path_factory.mktemp("b4_gold") / "gold"
    meterline_gold.build(silver=run, org=Path(run) / "org", policy=base / "policy.example.yaml",
                         prices=base / "prices.example.yaml", key=KEY, out=out)
    return out


def readme_labels():
    """The first cell of every row of the table under 'What a build writes', without backticks."""
    text = README.read_text(encoding="utf-8")
    section = text.split("## What a build writes", 1)[1].split("\n## ", 1)[0]
    return [re.match(r"\|\s*`([^`]+)`", line).group(1) for line in section.splitlines()
            if re.match(r"\|\s*`[^`]+`\s*\|", line)]


def label_of(rel):
    first = rel.split("/")[0]
    if rel == "manifest.json":
        return "manifest.json"
    return {"central": "central/", "exec": "exec/", "delivery": "delivery/", "manager": "manager/<manager_key>/",
            "person": "person/<person_key>/"}[first]


def disagreements(folder, servable):
    """(path, audience, got, want) for every file of ``folder`` and every reader where ``servable`` differs from the README."""
    found = []
    for path in sorted(p for p in Path(folder).rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        for audience in READERS:
            want = audience in README_SERVES[label_of(rel)]
            got = servable(rel, audience)
            if got != want:
                found.append((rel, audience, got, want))
    return found


# ---- done-check B4 ------------------------------------------------------------------------------------------


def test_done_check_B4_servable_agrees_with_the_readme_table_for_every_file(tiny_gold):
    import meterline_gold

    files = {label_of(p.relative_to(tiny_gold).as_posix()) for p in tiny_gold.rglob("*") if p.is_file()}
    assert files == set(README_SERVES)  # the build has a file of every row of the table
    assert disagreements(tiny_gold, meterline_gold.servable) == []


def test_done_check_B4_delivery_and_manifest_are_never_servable_to_a_reader(tiny_gold):
    import meterline_gold

    for rel in ("delivery/directory.csv", "manifest.json"):
        assert (tiny_gold / rel).is_file()
        for audience in ("exec", "manager", "person", "manager:pk_x", "person:pk_x"):
            assert meterline_gold.servable(rel, audience) is False, (rel, audience)
    assert meterline_gold.servable("delivery/directory.csv", "central") is False


def test_done_check_B4_a_wrong_rule_is_caught(tiny_gold):
    # negative control: the same comparison on a rule that serves the manifest to exec, and on one that serves exec
    # files to everyone
    import meterline_gold

    def lax(rel, audience):
        return True if rel == "manifest.json" else meterline_gold.servable(rel, audience)

    def wide(rel, audience):
        return True if rel.startswith("exec/") else meterline_gold.servable(rel, audience)

    assert {d[0] for d in disagreements(tiny_gold, lax)} == {"manifest.json"}
    assert {d[0].split("/")[0] for d in disagreements(tiny_gold, wide)} == {"exec"}


def test_the_readme_table_rows_are_the_patterns_of_audiences_json():
    from meterline_gold import audiences

    patterns = [rule["pattern"] for rule in audiences.load_rules()["rules"]]
    label_for = {"central/*": "central/", "exec/*": "exec/", "manager/{manager_key}/*": "manager/<manager_key>/",
                 "person/{person_key}/*": "person/<person_key>/", "delivery/*": "delivery/",
                 "manifest.json": "manifest.json"}
    assert sorted(patterns) == sorted(label_for)
    assert sorted(label_for[p] for p in patterns) == sorted(readme_labels())


def test_a_keyed_audience_reads_only_its_own_folder():
    import meterline_gold

    assert meterline_gold.servable("manager/pk_a/groups_month.csv", "manager") is True
    assert meterline_gold.servable("manager/pk_a/groups_month.csv", "manager:pk_a") is True
    assert meterline_gold.servable("manager/pk_b/groups_month.csv", "manager:pk_a") is False
    assert meterline_gold.servable("person/pk_a/person_month.csv", "person:pk_a") is True
    assert meterline_gold.servable("person/pk_b/person_month.csv", "person:pk_a") is False
    assert meterline_gold.servable("person/pk_a/person_month.csv", "manager:pk_a") is False


def test_an_unsafe_or_unknown_path_is_not_servable_and_an_unknown_audience_is_an_error():
    import meterline_gold

    for rel in ("../exec/groups_month.csv", "/exec/groups_month.csv", "exec/../central/people.csv",
                "exec\\groups_month.csv", "exec", "exec/", "exec/a/b.csv", "other/x.csv", "", "Exec/x.csv"):
        for audience in READERS:
            assert meterline_gold.servable(rel, audience) is False, (rel, audience)
    assert meterline_gold.servable("./exec/groups_month.csv", "exec") is True
    with pytest.raises(ValueError):
        meterline_gold.servable("exec/groups_month.csv", "intern")
    with pytest.raises(ValueError):
        meterline_gold.servable("exec/groups_month.csv", "central:abc")


def test_audiences_json_is_shipped_committed_and_valid():
    from meterline_gold import GOLD_SCHEMA_VERSION, audiences, contract

    packaged = Path(audiences.__file__).resolve().parent / "audiences.json"
    committed = SCHEMAS / "gold-audiences.json"
    assert packaged.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8"), REGENERATE
    assert committed.read_text(encoding="utf-8") == contract.dumps(audiences.load_rules()), REGENERATE
    schema_file = SCHEMAS / "gold-audiences.schema.json"
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    assert schema_file.read_text(encoding="utf-8") == contract.dumps(contract.gold_schemas()[schema_file.name])
    jsonschema.Draft202012Validator.check_schema(schema)
    rules = json.loads(committed.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(rules)
    assert rules["gold_schema_version"] == GOLD_SCHEMA_VERSION
    for rule in rules["rules"]:
        assert set(rule) == {"pattern", "audience", "serve", "contains_person_keys", "hidden_marker"}
        assert rule["serve"] in ("audience-only", "central-only", "never")


def test_the_schema_rejects_a_rule_with_an_unknown_serve_value():
    # negative control
    from meterline_gold import audiences, contract

    schema = contract.gold_schemas()["gold-audiences.schema.json"]
    bad = json.loads(json.dumps(audiences.load_rules()))
    bad["rules"][0]["serve"] = "everyone"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(bad)


def _columns(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


def test_audiences_json_tells_the_truth_about_person_keys_and_the_hidden_marker(tiny_gold):
    from meterline_gold import audiences

    key_columns = {"person_key", "manager_key", "head_key"}
    for rule in audiences.load_rules()["rules"]:
        regex = re.compile("".join("[^/]+" if tok == "*" or tok.startswith("{") else re.escape(tok)
                                   for tok in re.split(r"(\*|\{[a-z_]+\})", rule["pattern"])))
        files = [p for p in tiny_gold.rglob("*") if p.is_file() and regex.fullmatch(p.relative_to(tiny_gold).as_posix())]
        assert files, rule["pattern"]
        has_key = False
        marked = False
        for p in files:
            rel = p.relative_to(tiny_gold).as_posix()
            if "/pk_" in "/" + rel:
                has_key = True
            if p.suffix == ".csv":
                columns = _columns(p)
                has_key = has_key or bool(key_columns & set(columns))
                marked = marked or "hidden" in columns
            else:
                has_key = has_key or "pk_" in p.read_text(encoding="utf-8")
        assert has_key == rule["contains_person_keys"], rule["pattern"]
        assert (rule["hidden_marker"] == "hidden") == marked, rule["pattern"]


def test_nothing_is_added_to_the_gold_folder(tiny_gold):
    assert not [p for p in tiny_gold.rglob("*") if "audiences" in p.name]


def test_the_cli_and_the_module_write_audiences_json(tmp_path):
    from meterline_gold import audiences, cli, contract

    by_cli, by_module = tmp_path / "cli", tmp_path / "module"
    assert cli.main(["schemas", "--out", str(by_cli)]) == 0
    contract.write_schemas(by_module)
    for folder in (by_cli, by_module):
        assert (folder / "gold-audiences.json").read_text(encoding="utf-8") == contract.dumps(audiences.load_rules())
        assert (folder / "gold-audiences.schema.json").is_file()
