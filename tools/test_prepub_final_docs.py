"""Final pre-publication round: the documentation gaps that two blind readers hit, each pinned by a phrase.

Each test reads a doc and checks the short phrases that carry one rule. Where a doc states a fact about code, the
test also checks the fact against the code, so the doc and the code cannot drift apart.

Run: .venv/bin/python -m pytest -q tools/test_prepub_final_docs.py
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def section(text, heading, level=2):
    """The body of one section: from its heading to the next heading of the same or a higher level."""
    marks = "#" * level
    start = text.index("\n%s %s\n" % (marks, heading)) + 1
    rest = text[start:].split("\n", 1)[1]
    end = re.search(r"^#{1,%d} " % level, rest, re.M)
    return rest[:end.start()] if end else rest


# T1-a: no organisation-wide unallocated share

def test_t1a_the_reference_unallocated_share_is_per_provider_and_never_summed_across_providers():
    body = section(read("docs", "how-to", "finops-kpis.md"), "Reference unallocated share", level=3)
    flat = re.sub(r"\s+", " ", body)
    assert "**One provider at a time.**" in flat
    assert "no organisation-wide reference unallocated share" in flat
    assert "per provider and month" in flat
    assert re.search(r"not summed or averaged across providers", flat), "reference share"
    assert re.search(r"`unallocated_share`[^.]*not summed or averaged across providers", flat), "data_quality metric"
    assert "not a headline figure" in flat
    assert re.search(r"one value per provider and month", flat)
    assert re.search(r"left-out provider[^.]*empty share, never 0", flat)


GUIDE = ("docs", "how-to", "add-a-provider.md")
CONTRACT = ("docs", "reference", "connector-contract.md")


def guide_steps():
    """The numbered steps of the guide, each as one line (a step is one paragraph)."""
    body = read(*GUIDE).split("\n## Steps\n", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\d+\. (.+)$", body, re.M)


# T3-1: upgrade pip in the fresh venv

def test_t3_1_step_3_makes_the_venv_and_upgrades_pip_before_the_first_editable_install():
    step = guide_steps()[2]
    assert "python3 -m venv .venv" in step
    assert ".venv/bin/python -m pip install --upgrade pip" in step
    assert ".venv/bin/python -m pip install -e " in step
    assert step.index("python3 -m venv .venv") < step.index("pip install --upgrade pip") < step.index("pip install -e ")
    assert "editable mode currently requires a setuptools-based build" in step
    assert not re.search(r"(?<!python -m )(?<!/)\bpip install -e", step), "a pip command that is not run by the venv's python"


def test_t3_1_agents_md_install_section_upgrades_pip_before_the_installs():
    install = section(read("AGENTS.md"), "Install")
    assert "--upgrade pip" in install, "AGENTS.md Install does not say to upgrade pip"
    assert install.index("--upgrade pip") < install.index("pip install -e")


# T3-2: the final gate needs every package and takes minutes

INSTALL_ALL = "for p in packages/*/pyproject.toml; do printf -- '-e %s[dev] ' \"${p%/pyproject.toml}\"; done"


def flat(text):
    return re.sub(r"\s+", " ", text)


def test_t3_2_the_guide_says_tools_imports_every_package_and_gives_one_install_command_and_the_duration():
    text = read(*GUIDE)
    gate = flat(guide_steps()[8] + " " + section(text, "Before the pull request"))
    assert "`pytest tools` imports every package" in gate
    assert INSTALL_ALL in gate
    assert ".venv/bin/python -m pytest -q -n 4 tools -p no:cacheprovider" in gate
    assert re.search(r"about \d+ minutes", gate)


def test_t3_2_agents_md_keeps_tools_out_of_the_fast_tier_and_gives_its_own_line_with_minutes():
    tests = section(read("AGENTS.md"), "Tests")
    fast = [line for line in tests.splitlines() if line.startswith("- **Fast tier**")]
    assert len(fast) == 1
    assert " tools" not in fast[0], "the fast tier still runs tools, which takes minutes"
    tools_line = [line for line in tests.splitlines() if "-n 4 tools" in line and "pytest" in line and "packages tools" not in line]
    assert tools_line, "no line gives the tools command on its own"
    assert re.search(r"about \d+ minutes", tools_line[0]) and "every package" in tools_line[0]


def test_t3_2_the_install_command_reaches_every_package_and_tools_imports_them_all():
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import package_list
    names = package_list.packages()
    assert names, "no package found"
    for name in names:
        assert "dev" in package_list.metadata(name).get("optional-dependencies", {}), "%s has no dev extra" % name
    scripts = read("tools", "test_console_scripts.py")
    assert 'PACKAGES.glob("*/pyproject.toml")' in scripts and "importlib.import_module(module)" in scripts


# T3-3: run the tools with the venv's python

TOOL_COMMAND = re.compile(r"(?<![\w/])tools/(?:new_connector|check_new_connector)\.py(?= (?:NAME|acme))")
VENV_PYTHON = ".venv/bin/python "


def test_t3_3_every_command_that_runs_a_tool_uses_the_venv_python_in_the_docs():
    for parts in (GUIDE, CONTRACT, ("AGENTS.md",)):
        text = read(*parts)
        assert not re.search(r"python3 tools/(?:new|check_new)_connector\.py", text), parts
        found = list(TOOL_COMMAND.finditer(text))
        assert found, parts
        for match in found:
            assert text[:match.start()].endswith(VENV_PYTHON), "%s: %r" % ("/".join(parts), text[max(0, match.start() - 40):match.end()])


def test_t3_3_the_guide_makes_the_venv_before_its_first_venv_python_command():
    text = read(*GUIDE)
    assert text.index("python3 -m venv .venv") < text.index(".venv/bin/python")
    assert "cannot be imported" in text


def test_t3_3_the_docstrings_of_the_two_tools_use_the_venv_python():
    for name in ("new_connector.py", "check_new_connector.py"):
        text = read("tools", name)
        assert "python3 tools/" not in text, name
        assert "python3 -m pytest" not in text, name
        for match in TOOL_COMMAND.finditer(text):
            assert text[:match.start()].endswith(VENV_PYTHON), "%s: %r" % (name, text[max(0, match.start() - 40):match.end()])


def test_t3_3_what_the_tools_print_names_the_venv_python(tmp_path):
    import contextlib
    import io
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import check_new_connector
    import new_connector
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        new_connector.main(["acme", "--out", str(tmp_path / "packages")])
    printed = out.getvalue()
    assert ".venv/bin/python -m pip install -e " in printed and ".venv/bin/python tools/check_new_connector.py" in printed, printed
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert check_new_connector.main(["nosuch"], root=str(tmp_path)) == 2
    assert ".venv/bin/python tools/new_connector.py nosuch" in err.getvalue(), err.getvalue()
    assert "`.venv/bin/python -m pytest -q tools`" in check_new_connector.render(
        str(tmp_path), "meterline-acme", [(check_new_connector.wiring.ITEMS[0], ["x"])], []), "the closing line of the list"


def test_t3_3_the_opening_block_installs_the_core_dev_extra_before_it_runs_new_connector():
    text = read(*GUIDE)
    block = re.search(r"```sh\n(python3 -m venv \.venv\n.*?)```", text, re.S).group(1)
    lines = block.strip().splitlines()
    wanted = ["python3 -m venv .venv", ".venv/bin/python -m pip install --upgrade pip",
              '.venv/bin/python -m pip install -e "packages/meterline[conformance,dev]"', ".venv/bin/python tools/new_connector.py acme"]
    assert lines == wanted, lines
    assert "`tomli`" in flat(text.split("```sh", 1)[0]), "the opening says why the dev extra comes first"
    step = guide_steps()[2]
    assert "[conformance,dev]" in step and "`tomli`" in step
    assert step.index("packages/meterline[conformance,dev]") < step.index("tools/new_connector.py") < step.index("packages/meterline-<provider>[dev]")


def test_t3_3_step_9_says_the_first_item_is_to_track_the_files_with_git_add():
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import wiring
    assert wiring.ITEMS[0].id == "git-tracked", "the checker no longer lists the tracked files first: update step 9"
    assert wiring.ITEMS[0].add(wiring.Package(ROOT, "meterline-acme")) == "git add packages/meterline-acme"
    step = guide_steps()[8]
    assert re.search(r"first item[^.]*`git add packages/meterline-<provider>`[^.]*tracked files only", step)


def test_t3_3_new_connector_does_not_crash_when_the_wiring_report_cannot_be_imported(tmp_path, monkeypatch, capsys):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import new_connector
    root = tmp_path.resolve()
    (root / "tools").mkdir()
    monkeypatch.setattr(new_connector, "HERE", root / "tools")
    monkeypatch.setitem(sys.modules, "check_new_connector", None)  # the import fails, as without tomli on Python 3.10
    assert new_connector.main(["acme", "--out", str(root / "packages")]) == 0
    assert (root / "packages" / "meterline-acme" / "pyproject.toml").exists()
    err = capsys.readouterr().err
    assert 'the wiring list needs the core package\'s dev extra (tomli on Python 3.10)' in err
    assert '.venv/bin/python -m pip install -e "packages/meterline[conformance,dev]"' in err
    assert ".venv/bin/python tools/check_new_connector.py meterline-acme" in err


# T3-7: the two README rules that were learned only by failing a test

def readme_rule(bullet_start):
    rules = section(read(*CONTRACT), "The README rules")
    found = [flat(b) for b in re.split(r"\n(?=- )", rules) if b.startswith("- " + bullet_start)]
    assert len(found) == 1, bullet_start
    return found[0]


def test_t3_7_the_runnable_example_must_start_a_line_with_the_command():
    bullet = readme_rule("the first 40 lines")
    assert "a line that starts with the command" in bullet
    assert "`meterline" in bullet and "`python" in bullet
    assert "not with a variable assignment" in bullet
    assert "`export" in bullet


def test_t3_7_the_45_word_limit_also_binds_records_md_and_the_learning_pages():
    bullet = readme_rule("every list and table")
    assert "`docs/records.md`" in bullet
    assert "`docs/tutorial.md`" in bullet and "`docs/how-to/add-a-provider.md`" in bullet


def test_t3_7_guide_step_8_says_the_records_md_section_keeps_sentences_to_45_words():
    step = guide_steps()[7]
    assert re.search(r"`docs/records.md` section[^.]*45 words", step) or re.search(r"45 words[^.]*`docs/records.md`", step)


def test_t3_7_the_rules_the_docs_state_still_hold_in_the_tests():
    readmes = read("tools", "test_package_readmes.py")
    anchor = re.search(r'runnable = \[b for b in blocks if re\.search\(r"([^"]+)", b, re\.M\)\]', readmes)
    assert anchor, "the first-screen test no longer has its runnable-example regex"
    pattern = re.compile(anchor.group(1), re.M)
    assert pattern.search("meterline-acme backfill --since 2026-01-01\n")
    assert pattern.search("python3 -m meterline_acme\n")
    assert not pattern.search("ACME_API_KEY=... meterline-acme backfill\n"), "a variable assignment now counts: update the doc"
    assert not pattern.search("export ACME_API_KEY=x\n")
    assert "FIRST_SCREEN = 40" in readmes
    prose = read("tools", "test_review_f24_prose.py")
    learning = re.search(r"LEARNING = \[(.*?)\]", prose, re.S).group(1)
    for page in ("docs/records.md", "docs/tutorial.md", "docs/glossary.md", "docs/architecture.md",
                 "docs/how-to/configure-policy.md", "docs/how-to/add-a-provider.md"):
        assert '"%s"' % page in learning, page
    assert "MAX_WORDS = 45" in prose


# T3-8: how the checker finds the key variable

def checklist():
    return flat(section(read(*CONTRACT), "The wiring checklist"))


def test_t3_8_the_checklist_says_how_the_checker_finds_the_key_variable():
    text = checklist()
    assert 'KEY_ENV = "ACME_API_KEY"' in text
    assert "src/*/cli.py`" in text and "ends in `KEY` or `TOKEN`" in text
    assert "<NAME>_API_KEY" in text
    assert "`SECURITY.md`" in text
    assert re.search(r"`KEY_ENV` must name the variable that the connector really reads", text)
    assert "admin key" in text


def test_t3_8_step_5_says_the_checker_reads_key_env_from_cli_py():
    step = guide_steps()[4]
    assert 'KEY_ENV = "' in step and "`cli.py`" in step
    assert "#the-wiring-checklist" in step
    assert "really reads" in step


def test_t3_8_the_checker_finds_the_key_variable_the_way_the_docs_say(tmp_path):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import wiring

    def found(cli_text):
        src = tmp_path / "packages" / "meterline-acme" / "src" / "meterline_acme"
        src.mkdir(parents=True, exist_ok=True)
        (src / "cli.py").write_text(cli_text, encoding="utf-8")
        package = wiring.Package(str(tmp_path), "meterline-acme")
        return package.declared_env_vars, package.env_vars

    assert found('KEY_ENV = "ACME_ADMIN_KEY"\n') == (["ACME_ADMIN_KEY"], ["ACME_ADMIN_KEY"])
    assert found('TOKEN_ENV = "ACME_TOKEN"\n')[0] == ["ACME_TOKEN"]
    assert found('KEY_ENV = "ACME_SECRET"\nBASE_ENV = "ACME_BASE_URL"\n') == ([], ["ACME_API_KEY"])
    assert found('    KEY_ENV = "ACME_ADMIN_KEY"\n')[0] == [], "the line must start in column 0"


# T3-9: compare_shape compares kinds

def test_t3_9_the_contract_says_compare_shape_compares_kinds_and_all_scalars_are_alike():
    text = flat(section(read(*CONTRACT), "Checking a fake against the shape files"))
    assert "object (a map), array (a list) or scalar" in text
    assert "a string, a number, a boolean or null" in text
    assert "A null where the shape file has a string is not a difference" in text
    assert "a map where the shape has a list is" in text


def test_t3_9_compare_shape_compares_kinds_as_the_contract_says():
    from meterline.conformance import compare_shape
    assert compare_shape({"a": None}, {"a": "x"}) == []
    assert compare_shape({"a": 1}, {"a": "x"}) == []
    assert compare_shape({"a": True}, {"a": "x"}) == []
    assert compare_shape({"a": "x"}, {"a": 1}) == []
    assert compare_shape({"a": {}}, {"a": []}) == ["$.a: expected array, got object"]
    assert compare_shape({"a": []}, {"a": {}}) == ["$.a: expected object, got array"]
    assert compare_shape({"a": "x"}, {"a": []}) == ["$.a: expected array, got scalar"]
    assert compare_shape({"a": [1]}, {"a": []}) == [], "an empty list in the shape is not compared"


# T3-10: what the kit does not check

NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")


def test_t3_10_the_unchecked_list_names_paging_window_finality_reread_and_summing_without_a_count():
    body = section(read(*CONTRACT), "What is not checked by a test")
    opening = body.strip().split("\n", 1)[0]
    assert opening.endswith(":"), opening
    assert not re.search(r"\d", opening) and not re.search(r"\b(%s)\b" % "|".join(NUMBER_WORDS), opening.lower()), opening
    bullets = " ".join(flat(b).lower() for b in re.findall(r"^- .*$", body, re.M))
    for topic in ("paging", "window", "`is_final`", "re-read", "summing"):
        assert topic in bullets, topic
    text = flat(body)
    assert "`tools/test_new_connector.py`" in text and "for the template only" in text
    assert "../how-to/add-a-provider.md#steps" in text
    step = guide_steps()[6]
    assert "paging" in step and "finality" in step and "#what-is-not-checked-by-a-test" in step


def test_t3_10_no_rule_of_the_kit_checks_what_the_list_says_it_does_not():
    import inspect
    from meterline.conformance import RULES
    words = re.compile(r"\b(page|pages|paging|paged|final|is_final|window|windows|sum|sums|summing|summed|re-read|reread)\b")
    body = flat(section(read(*CONTRACT), "What is not checked by a test"))
    for rule in RULES:
        if rule.id == "R19":
            # complete_through is about finality: R19 reads the connector's own is_final as given, and the list says so
            assert "R19 takes the connector's own rule as given (`Connector.is_final`)" in body
            continue
        described = "%s %s" % (rule.title, inspect.getdoc(rule.check) or "")
        assert not words.search(described.lower()), "%s now mentions %r: update the list of what the kit does not check" % (
            rule.id, words.search(described.lower()).group(0))
        assert "is_final" not in inspect.getsource(rule.check), "%s reads is_final: update the list" % rule.id
        assert not re.search(r"page_size|page_cap|next_page", inspect.getsource(rule.check)), "%s reads paging: update the list" % rule.id
