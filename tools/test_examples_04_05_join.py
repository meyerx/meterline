"""C-1 of the integrate part 2 change list: examples 04 and 05 join, so one reader can run the container example and then the dashboard
on the gold it published, without guessing a command.

Trial T1 of the first integrate pull request found five gaps: example 04's "Run it" block ended by removing its volumes, so no
published folder was left; example 05's "Run it" block assumed a local install and a hand-built gold root; the image held no
dashboard; nothing said how to serve the dashboard from a container; and the page of scenario B did not name the route. These tests
hold the five fixes: the volume removal sits in its own "Clean up" block, the image copies ``dashboard.py`` (and the build context lets
it in), example 05's README has a container block run after example 04's (and a server block that publishes its port on the host's
loopback only, with the no-sign-in warning beside it), the CI container job runs both blocks in that order and lets their exit code
count, and scenario B's page names the joined route.

Run: python3 -m pytest -q tools/test_examples_04_05_join.py
"""
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import readme_block  # noqa: E402

README_04 = "examples/04-container/README.md"
README_05 = "examples/05-dashboard/README.md"
PAGE_B = "docs/integrate/b-kubernetes.md"
RUN_IT_IN_CONTAINER = "Run it in the container"
SERVE_FROM_CONTAINER = "Serve it from the container"
CLEAN_UP = "Clean up"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def section(text, heading):
    """The text under a heading, up to the next heading of any level."""
    match = re.search(r"^#{1,6}\s+" + re.escape(heading) + r"\s*$", text, re.M)
    assert match, "no heading named: " + heading
    rest = text[match.end():]
    nxt = re.search(r"^#{1,6}\s", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def removes_volumes(block):
    return [l for l in block.splitlines() if re.search(r"docker\s+volume\s+(rm|remove|prune)\b", l)]


def unsafe_publishes(block):
    """The ``-p`` / ``--publish`` values of a block that are not bound to the host's loopback."""
    values = re.findall(r"(?:^|\s)(?:-p|--publish)(?:\s+|=)(\S+)", block)
    return [v for v in values if not v.startswith("127.0.0.1:")]


def steps():
    with open(ROOT / ".github" / "workflows" / "ci.yml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["jobs"]["container"]["steps"]


def index_of_step(needle):
    found = [i for i, s in enumerate(steps()) if needle in s.get("run", "")]
    assert len(found) == 1, "%r: found in %d steps" % (needle, len(found))
    return found[0]


# ---- example 04: the published volume stays, the removal is a separate block ----

def test_example_04_run_it_leaves_the_volumes_and_a_clean_up_block_removes_them():
    text = read(README_04)
    assert removes_volumes(readme_block.block_after(text, "Run it")) == []
    assert "docker volume create meterline-gold" in readme_block.block_after(text, "Run it")
    assert "docker volume rm meterline-data meterline-gold meterline-config" in readme_block.block_after(text, CLEAN_UP)
    assert text.index("## " + CLEAN_UP) > text.index("## Run it")


def test_negative_control_a_run_it_block_that_removes_its_volumes_is_caught():
    planted = "```sh\ndocker build .\ndocker volume rm meterline-data meterline-gold\n```\n"
    assert len(removes_volumes(planted)) == 1
    assert removes_volumes("```sh\ndocker volume create meterline-gold\n```\n") == []


# ---- the image carries the dashboard, and the build context lets it in ----

def dockerfile_copies(text):
    """[(sources, destination)] of the COPY lines that read the build context (not ``--from``), continuation lines joined."""
    joined = re.sub(r"\\\n", " ", text)
    out = []
    for line in joined.split("\n"):
        parts = line.strip().split()
        if parts and parts[0].upper() == "COPY" and not any(p.startswith("--from") for p in parts[1:]):
            args = [p for p in parts[1:] if not p.startswith("--")]  # a flag such as --chmod=0644 is not a source
            out.append((args[:-1], args[-1]))
    return out


def context_problems(dockerfile, ignore):
    """COPY sources that the ignore file keeps out of the build context (BuildKit would fail on them)."""
    allowed = [l[1:] for l in ignore.splitlines() if l.startswith("!")]
    bad = []
    for sources, _ in dockerfile_copies(dockerfile):
        for source in sources:
            if not any(source.startswith(a) for a in allowed):
                bad.append(source)
    return bad


def test_the_image_copies_the_dashboard_beside_the_demo_scripts():
    text = read("examples/04-container/Dockerfile")
    (sources, destination), = [c for c in dockerfile_copies(text) if c[1] == "/app/"]
    assert "examples/05-dashboard/dashboard.py" in sources
    assert "examples/04-container/prepare_demo.py" in sources and "examples/04-container/check_published.py" in sources


def test_the_build_context_lets_every_copied_file_in_and_the_two_ignore_files_stay_equal():
    text = read("examples/04-container/Dockerfile")
    for name in (".dockerignore", "Dockerfile.dockerignore"):
        assert context_problems(text, read("examples/04-container/" + name)) == [], name
    assert read("examples/04-container/.dockerignore") == read("examples/04-container/Dockerfile.dockerignore")


def test_negative_control_a_copy_that_the_ignore_file_keeps_out_is_caught():
    ignore = "*\n!packages/\n!examples/04-container/\n"
    assert context_problems("COPY examples/05-dashboard/dashboard.py /app/\n", ignore) == ["examples/05-dashboard/dashboard.py"]
    assert context_problems("COPY packages/ /src/packages/\n", ignore) == []


# ---- example 05: the container block, run after example 04's ----

def test_example_05_keeps_its_local_install_block_and_gains_the_container_block():
    text = read(README_05)
    assert ".venv/bin/python examples/05-dashboard/dashboard.py" in readme_block.block_after(text, "Run it")
    block = readme_block.block_after(text, RUN_IT_IN_CONTAINER)
    assert "docker run" in block and "-v meterline-gold:/gold" in block and "meterline-job:local" in block
    assert "/gold/published/manager" in block, "the block must list the keys of published/manager/"
    assert "--check" in block and "--audience" in block
    assert removes_volumes(block) == [] and "METERLINE_GOLD_KEY" not in block, "the dashboard needs no gold key"
    assert "example 04" in section(text, RUN_IT_IN_CONTAINER).lower()


def test_example_05_serves_from_the_container_on_the_hosts_loopback_only_and_warns_about_the_missing_sign_in():
    text = read(README_05)
    block = readme_block.block_after(text, SERVE_FROM_CONTAINER)
    assert "--host 0.0.0.0" in block
    assert "127.0.0.1:8080:8080" in block and unsafe_publishes(block) == []
    assert "no sign-in" in section(text, SERVE_FROM_CONTAINER).lower()


def test_negative_control_a_port_published_on_every_interface_is_caught():
    assert unsafe_publishes("docker run -p 8080:8080 image") == ["8080:8080"]
    assert unsafe_publishes("docker run --publish 0.0.0.0:8080:8080 image") == ["0.0.0.0:8080:8080"]
    assert unsafe_publishes("docker run -p 127.0.0.1:8080:8080 image") == []


# ---- the CI container job runs both blocks in order and the exit code counts ----

def test_the_container_job_runs_example_05s_block_after_example_04s_and_before_the_clean_up():
    run_04 = index_of_step('"Run it" --readme examples/04-container/README.md')
    run_05 = index_of_step('"%s" --readme examples/05-dashboard/README.md' % RUN_IT_IN_CONTAINER)
    clean_up = index_of_step('"%s" --readme examples/04-container/README.md' % CLEAN_UP)
    assert run_04 < run_05 < clean_up


def test_the_exit_code_of_the_dashboard_block_counts():
    step = steps()[index_of_step('"%s" --readme examples/05-dashboard/README.md' % RUN_IT_IN_CONTAINER)]
    assert "continue-on-error" not in step and "|| true" not in step["run"] and "set -euo pipefail" in step["run"]
    assert re.search(r"bash -e", step["run"])


# ---- scenario B's page names the joined route ----

def test_scenario_b_names_the_joined_route():
    text = read(PAGE_B).replace("\n", " ")
    assert "example 04, then example 05" in text and "same gold volume" in text
    assert "examples/05-dashboard/README.md#run-it-in-the-container" in text
