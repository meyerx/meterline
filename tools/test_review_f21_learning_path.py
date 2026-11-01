"""F-21: a tutorial with every command and its expected output, and how-to guides for a policy and a provider.

The tutorial's simulator and gold commands are run here, and what they print must equal the output block the
tutorial shows under them, so the tutorial cannot drift from the tools. The policy guide's worked example is
loaded by gold's own policy loader, and its broken example must fail with the message the guide quotes.

Run: python3 -m pytest -q tools/test_review_f21_learning_path.py
"""
import contextlib
import io
import os
import re
import shlex

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUTORIAL = os.path.join(ROOT, "docs", "tutorial.md")
POLICY_HOWTO = os.path.join(ROOT, "docs", "how-to", "configure-policy.md")
PROVIDER_HOWTO = os.path.join(ROOT, "docs", "how-to", "add-a-provider.md")
GOLD_KEY = "a-key-of-at-least-32-bytes-that-you-keep-out-of-git"


def read(path):
    assert os.path.isfile(path), "%s does not exist" % os.path.relpath(path, ROOT)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def blocks(text):
    """(language, body) of every fenced block, in order."""
    return [(m.group(1), m.group(2)) for m in re.finditer(r"^```(\w*)\n(.*?)^```", text, re.M | re.S)]


def command_and_output(text, program):
    """The one command line that runs ``program`` and the ``text`` block that follows its ``sh`` block."""
    found = blocks(text)
    for i, (lang, body) in enumerate(found):
        lines = [x for x in body.splitlines() if x.strip().startswith(".venv/bin/" + program + " ")]
        if lang == "sh" and lines:
            assert len(lines) == 1, program
            assert i + 1 < len(found) and found[i + 1][0] == "text", "no output block after the %s command" % program
            return lines[0].strip(), found[i + 1][1]
    raise AssertionError("the tutorial has no %s command" % program)


def run_main(main, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, err.getvalue() + out.getvalue()


def test_tutorial_covers_install_simulator_gold_and_dashboard_in_order():
    text = read(TUTORIAL)
    marks = ["pip install", ".venv/bin/meterline-sim generate", ".venv/bin/meterline-gold build",
             "python3 -m http.server"]
    places = [text.find(m) for m in marks]
    assert all(p >= 0 for p in places), dict(zip(marks, places))
    assert places == sorted(places), "the steps are out of order"


def test_tutorial_shows_an_output_block_after_every_command_block():
    found = blocks(read(TUTORIAL))
    for i, (lang, body) in enumerate(found):
        if lang == "sh" and ("meterline-" in body or "http.server" in body):
            assert i + 1 < len(found) and found[i + 1][0] == "text", body


def test_tutorial_commands_print_what_the_tutorial_shows(tmp_path, monkeypatch):
    from meterline_gold.cli import main as gold_main
    from meterline_sim.cli import main as sim_main

    text = read(TUTORIAL)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("METERLINE_GOLD_KEY", GOLD_KEY)
    for program, main in (("meterline-sim", sim_main), ("meterline-gold", gold_main)):
        line, shown = command_and_output(text, program)
        argv = [os.path.join(ROOT, a) if a.startswith("packages/") else a for a in shlex.split(line)[1:]]
        code, printed = run_main(main, argv)
        assert code == 0, printed
        assert sorted(printed.splitlines()) == sorted(shown.splitlines()), program


def _yaml_blocks(text):
    return [body for lang, body in blocks(text) if lang == "yaml"]


def test_policy_howto_worked_example_is_a_valid_policy(tmp_path):
    from meterline_gold.params import load_policy

    examples = _yaml_blocks(read(POLICY_HOWTO))
    assert examples, "the policy guide has no yaml example"
    path = tmp_path / "policy.yaml"
    path.write_text(examples[0], encoding="utf-8")
    policy = load_policy(path)
    assert policy.scenarios, "the worked example should show a scenario"


def test_policy_howto_quotes_the_error_its_broken_example_gives(tmp_path):
    from meterline_gold.params import load_policy

    text = read(POLICY_HOWTO)
    good = _yaml_blocks(text)[0]
    change = re.search(r"<!-- broken: (.*?) -> (.*?) -->", text)
    assert change, "the guide marks the one-line change of its broken example"
    assert change.group(1) in good
    path = tmp_path / "bad.yaml"
    path.write_text(good.replace(change.group(1), change.group(2)), encoding="utf-8")
    with pytest.raises(Exception) as caught:
        load_policy(path)
    quoted = re.search(r"month_cost_ref_above is allowed for audience central only", text)
    assert quoted and quoted.group(0) in str(caught.value)


def test_add_a_provider_howto_is_ordered_steps_with_the_shape_and_done_check_pattern():
    text = read(PROVIDER_HOWTO)
    steps = re.findall(r"^(\d+)\. ", text, re.M)
    assert len(steps) >= 6
    for word in ("docs/api-shapes/", "test_done_check_", "fetch", "normalis", "records.md", "## Known limits"):
        assert word in text, word
