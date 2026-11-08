"""R-1, R-2, R-3 and R-6 of the integrate part 1 spec: the integration pages, ``scenarios.yaml`` and the links to them.

A scenario page has a steps table (``| Step | How | Maturity |``) whose Maturity cell opens with one of four marks, from most to
least mature: verified, built, pattern, new code. A step marked built or verified must name an example that CI runs or a
console script of a package. Every step of D and E is pattern or new code. ``scenarios.yaml`` lists the same steps and must
agree with the pages. The test fails, and does not skip, when PyYAML or jsonschema is missing.

Run: python3 -m pytest -q tools/test_integrate_docs.py
"""
import json
import os
import re

import jsonschema  # a missing package is a failing test, never a skipped one
import pytest
import yaml

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKS = ["verified", "built", "pattern", "new code"]  # most to least mature
PAGES = {"A": "docs/integrate/a-one-machine.md", "B": "docs/integrate/b-kubernetes.md", "C": "docs/integrate/c-lake-and-warehouse.md",
         "D": "docs/integrate/d-gold-in-warehouse.md", "E": "docs/integrate/e-agents.md"}
BASE = "docs/integrate/README.md"
YAML_PATH, SCHEMA_PATH = "docs/integrate/scenarios.yaml", "docs/schemas/integrate/scenarios.schema.json"
GITHUB = "https://github.com/meyerx/meterline/blob/main/"
HEADER = re.compile(r"^\|\s*Step\s*\|\s*How\s*\|\s*Maturity\s*\|\s*$", re.M)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def mark_of(cell):
    """The mark a Maturity cell opens with, or None."""
    cell = cell.strip().lower()
    return next((m for m in MARKS if re.match(re.escape(m) + r"\b", cell)), None)


def steps_of(text):
    """[(step, how, maturity cell)] of the steps table of a page."""
    match = HEADER.search(text)
    assert match, "no steps table"
    rows = []
    for line in text[match.end():].lstrip("\n").split("\n")[1:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 3, line
        rows.append(tuple(cells))
    return rows


def least_mature(marks):
    return max(marks, key=MARKS.index)


def console_scripts():
    found = set()
    for name in os.listdir(os.path.join(ROOT, "packages")):
        path = os.path.join(ROOT, "packages", name, "pyproject.toml")
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                found |= set(tomllib.load(fh).get("project", {}).get("scripts", {}))
    return found


def examples_run_by_ci():
    """The example folders that CI runs: one that the CI workflow or tools/test_examples.py or an example test names."""
    names = sorted(os.listdir(os.path.join(ROOT, "examples")))
    sources = read(".github/workflows/ci.yml") + read("tools/test_examples.py")
    return {n for n in names if os.path.isdir(os.path.join(ROOT, "examples", n)) and n in sources}


def names_a_runnable_thing(how):
    """True when the text names an example CI runs or a console script of a package."""
    ci = examples_run_by_ci()
    if any(m in ci for m in re.findall(r"examples/(\d\d-[a-z0-9-]+)", how)):
        return True
    return any(re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(script), how) for script in console_scripts())


def problems_in_steps(rows, only_pattern_or_new):
    bad = []
    for step, how, cell in rows:
        mark = mark_of(cell)
        if mark is None:
            bad.append("%s: the maturity %r opens with none of the four marks" % (step, cell))
        elif mark in ("built", "verified") and only_pattern_or_new:
            bad.append("%s: a step of D or E cannot be marked %s" % (step, mark))
        elif mark in ("built", "verified") and not names_a_runnable_thing(how):
            bad.append("%s: marked %s but names no example that CI runs and no console script" % (step, mark))
    return bad


def test_the_base_page_and_the_five_scenario_pages_exist():
    for rel in [BASE, *PAGES.values()]:
        assert os.path.isfile(os.path.join(ROOT, rel)), rel


@pytest.mark.parametrize("scenario", sorted(PAGES))
def test_every_step_has_a_mark_and_a_built_step_names_something_that_runs(scenario):
    rows = steps_of(read(PAGES[scenario]))
    assert rows
    assert problems_in_steps(rows, only_pattern_or_new=scenario in ("D", "E")) == []


@pytest.mark.parametrize("scenario", sorted(PAGES))
def test_the_page_states_its_least_mature_step_and_it_is_the_last_mark_in_the_order(scenario):
    text = read(PAGES[scenario])
    stated = re.search(r"\*\*Least mature step:\*\* (verified|built|pattern|new code)\b", text)
    assert stated, "no 'Least mature step' line"
    assert stated.group(1) == least_mature([mark_of(c) for _, _, c in steps_of(text)])


def test_negative_control_a_d_step_marked_built_is_caught():
    # the predicate of the test above, run on a planted table: a built step in D, and a built step that names nothing
    planted = "| Step | How | Maturity |\n|---|---|---|\n| Port | rewrite the SQL | built |\n| Load | a script | pattern |\n"
    assert problems_in_steps(steps_of(planted), only_pattern_or_new=True) == ["Port: a step of D or E cannot be marked built"]
    assert len(problems_in_steps(steps_of(planted), only_pattern_or_new=False)) == 1
    assert problems_in_steps(steps_of("| Step | How | Maturity |\n|---|---|---|\n| Run | `meterline-job run CONFIG` | built |\n"), False) == []


STALE = {
    "the draft's stage_silver function": r"stage_silver",
    "cron flock": r"\bflock\b",
    "the latest gold folder as a dashboard source": r"latest gold folder",
    "gold's own exit codes 1 and 2": r"[Gg]old exits 1",
    "a copy of example 03 in the image": r"copies example 03",
    "a claim that none has run end to end": r"none has been run end to end",
    "the draft's rule to load with the key column for a row access policy": r"so that the row access policy can filter",
    "the draft's single table per audience folder": r"load into one table each",
}


@pytest.mark.parametrize("passage", sorted(STALE))
def test_no_page_keeps_a_stale_passage_of_the_draft(passage):
    for rel in [BASE, *PAGES.values()]:
        assert not re.search(STALE[passage], read(rel)), "%s: %s" % (rel, passage)


def test_the_base_page_says_what_the_job_gives_and_what_a_run_must_never_serve():
    text = read(BASE)
    for needle in ("meterline-job run CONFIG --step silver", "published/", "lock", "exit 4", "never retried",
                   "servable(", "delivery/", "verified", "built", "pattern", "new code", "What meterline could add",
                   "C, D and E"):
        assert needle in text, needle
    assert "none of C, D and E has been run end to end" in text.replace("\n", " ")


def test_the_additions_table_dropped_what_is_built_and_kept_what_is_not():
    table = read(BASE).split("What meterline could add", 1)[1]
    for built in ("A supported job command", "An `auto` command", "A lock per data folder"):
        assert built not in table, built
    for kept in ("loader", "alert hook", "email sender", "engine interface", "Portable SQL", "MCP server"):
        assert kept in table, kept
    assert "published container image" in table and "reference Python dashboard" not in table


def test_b_says_the_lock_is_tested_on_local_disk_only_and_one_job_at_a_time():
    text = read(PAGES["B"]).replace("\n", " ")
    assert "local disk" in text and "network disk" in text and "one job at a time per data folder" in text


def test_the_pages_hold_no_trace_of_the_process_that_decided_them():
    pattern = re.compile(r"\b(METR|SUP)-\d+|Supervisor|research/|/Users/|\bXavier\b", re.I)
    for rel in [BASE, *PAGES.values(), YAML_PATH]:
        found = pattern.findall(read(rel))
        assert not found, "%s: %s" % (rel, found)


# ---- R-3: scenarios.yaml ----

def scenarios():
    return yaml.safe_load(read(YAML_PATH))["scenarios"]


def test_scenarios_yaml_validates_against_its_schema():
    schema = json.loads(read(SCHEMA_PATH))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(yaml.safe_load(read(YAML_PATH)), schema)
    assert [s["id"] for s in scenarios()] == ["A", "B", "C", "D", "E"]


def test_the_schema_rejects_an_unknown_mark_and_a_missing_field():
    schema = json.loads(read(SCHEMA_PATH))
    good = yaml.safe_load(read(YAML_PATH))
    bad_mark = json.loads(json.dumps(good))
    bad_mark["scenarios"][0]["steps"][0]["mark"] = "mostly built"
    missing = json.loads(json.dumps(good))
    del missing["scenarios"][1]["gold_runs"]
    for planted in (bad_mark, missing):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(planted, schema)


def test_every_page_and_example_the_yaml_names_exists():
    for scenario in scenarios():
        assert os.path.isfile(os.path.join(ROOT, scenario["page"])), scenario["page"]
        for step in scenario["steps"]:
            if step["example"] is not None:
                assert os.path.isdir(os.path.join(ROOT, step["example"])), step["example"]
    assert {s["id"]: s["page"] for s in scenarios()}["C"] == PAGES["C"]


def test_least_mature_is_the_last_mark_in_the_order_of_the_steps():
    for scenario in scenarios():
        assert scenario["least_mature"] == least_mature([s["mark"] for s in scenario["steps"]]), scenario["id"]


def test_the_yaml_steps_follow_the_same_rules_as_the_pages():
    for scenario in scenarios():
        rows = [(str(i), s["how"] + (" " + s["example"] if s["example"] else ""), s["mark"]) for i, s in enumerate(scenario["steps"], 1)]
        assert problems_in_steps(rows, only_pattern_or_new=scenario["id"] in ("D", "E")) == [], scenario["id"]


def test_page_and_yaml_agree_on_every_mark():
    by_id = {s["id"]: s for s in scenarios()}
    for scenario_id, rel in PAGES.items():
        on_page = [mark_of(cell) for _, _, cell in steps_of(read(rel))]
        assert on_page == [s["mark"] for s in by_id[scenario_id]["steps"]], scenario_id
        assert by_id[scenario_id]["page"] == rel


def test_the_tests_job_installs_pyyaml_and_jsonschema():
    install = re.search(r"- name: Install every package \(editable\).*?run: \|\n(.*?)\n      - name:", read(".github/workflows/ci.yml"), re.S)
    assert install and "pyyaml" in install.group(1).lower() and "jsonschema" in install.group(1).lower()


# ---- R-6: entry points ----

def links(text):
    return re.findall(r"\]\(([^)\s]+)\)", text)


@pytest.mark.parametrize("source, target", [
    ("README.md", "docs/integrate/README.md"),
    ("docs/README.md", "integrate/README.md"),
    ("AGENTS.md", "docs/integrate/README.md"),
    ("AGENTS.md", "docs/integrate/scenarios.yaml"),
    ("llms.txt", GITHUB + "docs/integrate/README.md"),
    ("llms.txt", GITHUB + "docs/integrate/scenarios.yaml"),
    ("examples/03-daily-job/README.md", GITHUB + "docs/integrate/README.md"),
])
def test_each_entry_point_links_its_exact_target(source, target):
    assert target in links(read(source)), "%s does not link %s" % (source, target)


# ---- part 2: scenario C's page, its warehouse rules and its excerpts ----

C_EXAMPLES = ("examples/06-warehouse-duckdb", "examples/07-person-messages")
EXCERPTS = {"Snowflake": "grants", "S3": "bucket policy", "SES": "sender identity"}


def section_of(text, heading):
    """The text under a ``###`` heading of the page, up to the next heading of the same or a higher level (a line inside a code
    fence is never a heading: a shell comment starts with ``#``)."""
    lines, found, out, fenced = text.split("\n"), False, [], False
    for line in lines:
        if line.startswith("```"):
            fenced = not fenced
        if found:
            if not fenced and re.match(r"^#{1,3}\s", line):
                break
            out.append(line)
        elif not fenced and re.match(r"^###\s+%s\b" % re.escape(heading), line):
            found = True
            out.append(line)
    assert found, "no heading for " + heading
    return "\n".join(out)


EXCERPT_WORDS = re.compile(r"\b(Snowflake|S3|Iceberg|SES)\b")


def excerpt_step_problems(steps):
    """The steps (dicts with how, mark and example) that load into Snowflake or S3, or send through SES, and are not a pattern."""
    return [s["how"] for s in steps if EXCERPT_WORDS.search(s["how"]) and not (s["mark"] == "pattern" and s["example"] is None)]


def test_c_names_examples_06_and_07_for_its_built_steps_and_marks_every_excerpt_step_pattern():
    (scenario,) = [s for s in scenarios() if s["id"] == "C"]
    built = [s for s in scenario["steps"] if s["mark"] == "built"]
    assert {s["example"] for s in built} >= set(C_EXAMPLES), built
    assert excerpt_step_problems(scenario["steps"]) == []
    rows = steps_of(read(PAGES["C"]))
    on_page = [{"how": how, "mark": mark_of(cell), "example": None} for _, how, cell in rows]
    assert excerpt_step_problems(on_page) == []
    assert {name for step in on_page for name in ("Snowflake", "S3", "SES") if re.search(r"\b%s\b" % name, step["how"])} == {"Snowflake", "S3", "SES"}
    page_examples = {m for _, how, cell in rows if mark_of(cell) == "built" for m in re.findall(r"examples/(\d\d-[a-z0-9-]+)", how)}
    assert {e.split("/")[1] for e in C_EXAMPLES} <= page_examples, page_examples


def test_negative_control_an_excerpt_step_marked_built_is_caught():
    planted = [{"how": "load into Snowflake with the excerpt", "mark": "built", "example": "examples/06-warehouse-duckdb"},
               {"how": "send through SES", "mark": "pattern", "example": None}, {"how": "write the files", "mark": "built", "example": None}]
    assert excerpt_step_problems(planted) == ["load into Snowflake with the excerpt"]


def test_c_is_pattern_at_its_least_mature_step_on_the_page_the_yaml_and_the_base_table():
    by_id = {s["id"]: s for s in scenarios()}
    assert by_id["C"]["least_mature"] == "pattern"
    assert re.search(r"\*\*Least mature step:\*\* pattern\b", read(PAGES["C"]))
    table = {}
    for line in read(BASE).split("\n"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        match = re.match(r"^\[?([A-E])\. ", cells[0]) if len(cells) == 5 else None
        if match:
            table[match.group(1)] = (cells[0], cells[4])
    assert sorted(table) == ["A", "B", "C", "D", "E"]
    for scenario_id, (first, least) in table.items():
        assert least == by_id[scenario_id]["least_mature"], scenario_id
        assert ("(%s)" % os.path.basename(PAGES[scenario_id])) in first, scenario_id


def test_c_states_the_warehouse_rules():
    text = read(PAGES["C"]).replace("\n", " ")
    for needle in ("one schema per audience", "no role that holds", "replaced at each build", "time travel", "no kept snapshots",
                   "secure views", "connection", "without names", "restricted schema", "silver", "platform administrators",
                   "delivery/", "container task", "Lambda"):
        assert needle in text, needle
    assert re.search(r"manager, exec and person tables", text)
    assert "example 06" in text.lower() and "example 07" in text.lower()


def test_each_excerpt_is_commented_marked_pattern_to_assemble_and_names_what_the_adopter_adds():
    text = read(PAGES["C"])
    for name, must_add in EXCERPTS.items():
        section = section_of(text, name)
        assert "pattern" in section and "to assemble" in section, name
        assert must_add in section.lower(), (name, must_add)
        blocks = re.findall(r"```[a-z]*\n(.*?)```", section, re.S)
        assert blocks, name
        for block in blocks:
            code = [l for l in block.split("\n") if l.strip()]
            assert code and all(l.lstrip().startswith(("--", "#")) for l in code), "%s: a line of the excerpt is not a comment" % name


def test_the_excerpts_hold_no_secret_and_no_real_name():
    text = read(PAGES["C"])
    assert not re.search(r"(AKIA[0-9A-Z]{16}|aws_secret_access_key|password\s*=|BEGIN [A-Z ]*PRIVATE KEY)", text, re.I)
    assert not re.search(r"\b\d{12}\b", text), "a twelve-digit account number"


@pytest.mark.parametrize("source, target", [
    ("README.md", "docs/integrate/c-lake-and-warehouse.md"),
    ("docs/README.md", "integrate/c-lake-and-warehouse.md"),
    ("AGENTS.md", "docs/integrate/c-lake-and-warehouse.md"),
    ("llms.txt", GITHUB + "docs/integrate/c-lake-and-warehouse.md"),
    ("docs/README.md", "../examples/06-warehouse-duckdb/README.md"),
    ("docs/README.md", "../examples/07-person-messages/README.md"),
    ("llms.txt", GITHUB + "examples/06-warehouse-duckdb/README.md"),
    ("llms.txt", GITHUB + "examples/07-person-messages/README.md"),
])
def test_each_entry_point_reaches_scenario_c_and_examples_06_and_07(source, target):
    assert target in links(read(source)), "%s does not link %s" % (source, target)


@pytest.mark.parametrize("example", ["06-warehouse-duckdb", "07-person-messages"])
def test_agents_md_names_examples_06_and_07(example):
    assert "examples/%s/" % example in read("AGENTS.md")
