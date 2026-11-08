"""C-2 to C-5 of the integrate part 2 change list: the findings of the three documentation trials that ran after part 2's first pull request.

C-2  the image sets the mode of every file it copies into /app, so a clone made under umask 077 still builds an image whose user can read them.
C-3  examples 06 and 07 join example 04's gold volume: the image carries their scripts, each README has a container block run after
     example 04's "Run it", the container job runs both blocks after example 05's and before the "Clean up", and the C page names the route.
C-4  example 07's README and the C page say how the program runs each month, in a section marked pattern.
C-5  scenario D says what to do under a strict "no batch outside the warehouse" rule, and the docs index links every scenario page, D included.

Run: python3 -m pytest -q tools/test_trial_findings.py
"""
import fnmatch
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import readme_block  # noqa: E402

README_04 = "examples/04-container/README.md"
README_05 = "examples/05-dashboard/README.md"
README_06 = "examples/06-warehouse-duckdb/README.md"
README_07 = "examples/07-person-messages/README.md"
PAGE_C = "docs/integrate/c-lake-and-warehouse.md"
IN_THE_CONTAINER = "Run it in the container"
CLEAN_UP = "Clean up"
DOCKERFILE = "examples/04-container/Dockerfile"
READABLE_MODES = ("0644", "644", "0444", "444")


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ---- C-2: the image sets the modes of what it copies ----

def instructions(dockerfile):
    """The instructions of a Dockerfile as lists of words, continuation lines joined, comments and blank lines dropped."""
    joined = re.sub(r"\\\n", " ", dockerfile)
    return [l.split() for l in joined.split("\n") if l.strip() and not l.strip().startswith("#")]


def app_copies(dockerfile):
    """[(position, flags, sources, destination)] of the COPY instructions that read the build context into /app (not ``--from``)."""
    out = []
    for position, words in enumerate(instructions(dockerfile)):
        if words[0].upper() != "COPY":
            continue
        flags = [w for w in words[1:] if w.startswith("--")]
        rest = [w for w in words[1:] if not w.startswith("--")]
        if not any(f.startswith("--from") for f in flags) and rest[-1].startswith("/app"):
            out.append((position, flags, rest[:-1], rest[-1]))
    return out


def chmod_patterns(words):
    """The paths a ``RUN chmod <readable mode> PATH...`` instruction sets (nothing for any other instruction or any other mode)."""
    if words[0].upper() != "RUN":
        return []
    paths = []
    for segment in re.split(r"&&|;", " ".join(words[1:])):
        parts = segment.split()
        if parts[:1] == ["chmod"] and len(parts) > 2 and parts[1] in READABLE_MODES:
            paths += parts[2:]
    return paths


def copies_without_a_mode(dockerfile):
    """The files COPYed into /app that no later ``RUN chmod`` (before the first ``USER``) covers: they keep the mode they have in the clone."""
    words = instructions(dockerfile)
    problems = []
    for position, _, sources, destination in app_copies(dockerfile):
        covered = []
        for later in words[position + 1:]:
            if later[0].upper() == "USER":
                break
            covered += chmod_patterns(later)
        for source in sources:
            target = destination.rstrip("/") + "/" + source.rsplit("/", 1)[-1]
            if not any(fnmatch.fnmatchcase(target, pattern) for pattern in covered):
                problems.append((source, destination))
    return problems


def test_every_copy_into_app_is_followed_by_a_chmod_that_covers_its_files():
    text = read(DOCKERFILE)
    assert app_copies(text), "the Dockerfile copies nothing into /app: the check would pass on nothing"
    assert copies_without_a_mode(text) == []


def test_no_copy_into_app_uses_the_chmod_flag_which_the_legacy_builder_refuses():
    # `the --chmod option requires BuildKit`: a reader on Docker without buildx (the legacy builder) could not build the image
    flagged = [flags for _, flags, _, _ in app_copies(read(DOCKERFILE)) if any(f.startswith("--chmod") for f in flags)]
    assert flagged == []


def test_negative_control_a_copy_into_app_without_a_chmod_is_caught():
    covered = "COPY a/x.py a/y.py /app/\nRUN chmod 0644 /app/*.py\nUSER meterline\n"
    assert copies_without_a_mode(covered) == []
    bare = "COPY a/x.py /app/\nUSER meterline\n"
    assert copies_without_a_mode(bare) == [("a/x.py", "/app/")]
    late = "COPY a/x.py /app/\nUSER meterline\nRUN chmod 0644 /app/x.py\n"
    assert copies_without_a_mode(late) == [("a/x.py", "/app/")], "a chmod after USER runs as the image user and cannot change a root-owned file"
    narrow = "COPY a/x.py a/z.txt /app/\nRUN chmod 0644 /app/x.py\n"
    assert copies_without_a_mode(narrow) == [("a/z.txt", "/app/")]
    writable = "COPY a/x.py /app/\nRUN chmod 0666 /app/x.py\n"
    assert copies_without_a_mode(writable) == [("a/x.py", "/app/")]
    flag = "COPY --chmod=0644 a/x.py /app/\n"
    assert copies_without_a_mode(flag) == [("a/x.py", "/app/")] and app_copies(flag)[0][1] == ["--chmod=0644"]


# ---- C-3: examples 06 and 07 join example 04's gold volume ----

def section(text, heading):
    """The text under a heading, up to the next heading of any level."""
    match = re.search(r"^#{1,6}\s+" + re.escape(heading) + r"\s*$", text, re.M)
    assert match, "no heading named: " + heading
    rest = text[match.end():]
    nxt = re.search(r"^#{1,6}\s", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def removes_volumes(block):
    return [l for l in block.splitlines() if re.search(r"docker\s+volume\s+(rm|remove|prune)\b", l)]


def gold_mounts_not_read_only(block):
    """The ``-v meterline-gold:/gold...`` mounts of a block that do not end in ``:ro``: a reader of published/ has no use for write access."""
    return [m for m in re.findall(r"-v\s+(meterline-gold:/gold\S*)", block) if not m.endswith(":ro")]


def passes_send(block):
    return bool(re.search(r"(?:^|\s)--send\b", block))


def steps():
    with open(ROOT / ".github" / "workflows" / "ci.yml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["jobs"]["container"]["steps"]


def index_of_step(needle):
    found = [i for i, s in enumerate(steps()) if needle in s.get("run", "")]
    assert len(found) == 1, "%r: found in %d steps" % (needle, len(found))
    return found[0]


def test_the_image_carries_the_warehouse_loader_and_the_message_writer_and_two_folders_the_image_user_owns():
    text = read(DOCKERFILE)
    (_, _, sources, destination), = [c for c in app_copies(text) if "examples/04-container/prepare_demo.py" in c[2]]
    assert destination == "/app/"
    assert "examples/06-warehouse-duckdb/load_warehouse.py" in sources and "examples/07-person-messages/person_messages.py" in sources
    joined = re.sub(r"\\\n", " ", text)
    made = re.search(r"mkdir -p ([^&\n]+)", joined).group(1).split()
    owned = re.search(r"chown meterline ([^&\n]+)", joined).group(1).split()
    assert {"/warehouse", "/mail"} <= set(made) and {"/warehouse", "/mail"} <= set(owned), "a new volume takes the owner of the folder it is mounted on"


def test_example_06_keeps_its_host_block_and_gains_the_container_block():
    text = read(README_06)
    assert ".venv/bin/python examples/06-warehouse-duckdb/load_warehouse.py" in readme_block.block_after(text, "Run it")
    block = readme_block.block_after(text, IN_THE_CONTAINER)
    assert "/app/load_warehouse.py" in block and "meterline-job:local" in block and "--check" in block and "--connections" in block
    assert "docker volume create meterline-warehouse" in block and "-v meterline-warehouse:/warehouse" in block and "--out /warehouse" in block
    assert "/gold/published/manager" in block, "the block must list the keys of published/manager/"
    assert removes_volumes(block) == [] and "METERLINE_GOLD_KEY" not in block, "the loader needs no gold key"
    assert "example 04" in section(text, IN_THE_CONTAINER).lower()


def test_example_07_keeps_its_host_block_and_gains_the_container_block_that_never_sends():
    text = read(README_07)
    assert ".venv/bin/python examples/07-person-messages/person_messages.py" in readme_block.block_after(text, "Run it")
    block = readme_block.block_after(text, IN_THE_CONTAINER)
    assert "/app/person_messages.py" in block and "meterline-job:local" in block
    assert "docker volume create meterline-mail" in block and "-v meterline-mail:/mail" in block and "--out /mail/messages" in block
    assert not passes_send(block), "the container block writes files and never sends"
    assert removes_volumes(block) == [] and "METERLINE_GOLD_KEY" not in block, "the message writer needs no gold key"
    assert "example 04" in section(text, IN_THE_CONTAINER).lower()


def test_both_blocks_read_the_gold_volume_read_only():
    for readme in (README_06, README_07):
        block = readme_block.block_after(read(readme), IN_THE_CONTAINER)
        assert "meterline-gold:/gold" in block, readme
        assert gold_mounts_not_read_only(block) == [], readme


def test_negative_control_a_writable_gold_mount_and_a_send_are_caught():
    assert gold_mounts_not_read_only("docker run -v meterline-gold:/gold image") == ["meterline-gold:/gold"]
    assert gold_mounts_not_read_only("docker run -v meterline-gold:/gold:ro image") == []
    assert passes_send("python person_messages.py /gold --out /mail --send") and not passes_send("python person_messages.py /gold --out /mail")


def test_example_04_clean_up_also_removes_the_two_volumes_the_new_blocks_create_when_they_exist():
    block = readme_block.block_after(read(README_04), CLEAN_UP)
    assert "docker volume rm meterline-data meterline-gold meterline-config" in block
    for volume in ("meterline-warehouse", "meterline-mail"):
        assert volume in block, volume
    assert "docker volume inspect" in block and "|| true" not in block, "a volume that is absent is skipped; a removal that fails is not"
    assert "examples 05, 06 and 07" in section(read(README_04), CLEAN_UP)


def test_the_container_job_runs_06_and_07_after_05_and_before_the_clean_up():
    run_04 = index_of_step('"Run it" --readme examples/04-container/README.md')
    run_05 = index_of_step('"%s" --readme %s' % (IN_THE_CONTAINER, README_05))
    run_06 = index_of_step('"%s" --readme %s' % (IN_THE_CONTAINER, README_06))
    run_07 = index_of_step('"%s" --readme %s' % (IN_THE_CONTAINER, README_07))
    clean_up = index_of_step('"%s" --readme %s' % (CLEAN_UP, README_04))
    assert run_04 < run_05 < run_06 < run_07 < clean_up


def test_the_exit_codes_of_the_06_and_07_blocks_count():
    for readme in (README_06, README_07):
        step = steps()[index_of_step('"%s" --readme %s' % (IN_THE_CONTAINER, readme))]
        assert "continue-on-error" not in step and "|| true" not in step["run"] and "set -euo pipefail" in step["run"], readme
        assert re.search(r"bash -eo pipefail", step["run"]), readme


def test_scenario_c_names_the_joined_route():
    text = read(PAGE_C).replace("\n", " ")
    assert "example 04, then example 06 and example 07" in text and "same gold volume" in text
    assert "examples/06-warehouse-duckdb/README.md#run-it-in-the-container" in text
    assert "examples/07-person-messages/README.md#run-it-in-the-container" in text


# ---- C-4: example 07 says how it runs each month ----

MONTHLY = "Run it each month (pattern)"


def monthly_problems(text):
    """What the monthly section lacks: the heading with its mark, the trigger after the publish step, --month, and the record of months sent."""
    if not re.search(r"^#{2,3}\s+" + re.escape(MONTHLY) + r"\s*$", text, re.M):
        return ["no section headed: " + MONTHLY]
    body = " ".join(section(text, MONTHLY).split()).lower()
    wanted = {
        "once a month": "once a month",
        "publish step has frozen the month": "after the job's publish step has frozen the month",
        "a monthly cronjob or a scheduler of your choice": "a monthly CronJob or a scheduler",
        "--month": "--month set to that month",
        "does not keep": "the record of months sent, which the program does not keep",
    }
    return [what for needle, what in wanted.items() if needle not in body]


def test_example_07_says_how_it_runs_each_month():
    assert monthly_problems(read(README_07)) == []


def test_scenario_c_says_how_the_messages_run_each_month():
    assert monthly_problems(read(PAGE_C)) == []


def test_negative_control_a_section_without_the_trigger_or_the_record_is_caught():
    assert monthly_problems("## Other\n\ntext\n") == ["no section headed: " + MONTHLY]
    thin = "## %s\n\nRun it once a month with --month.\n" % MONTHLY
    assert len(monthly_problems(thin)) == 3
    full = ("## %s\n\nRun it once a month, after the job's publish step has frozen the month: a monthly CronJob or a scheduler of your choice, with "
            "--month set to it. The program does not keep the record of months sent.\n") % MONTHLY
    assert monthly_problems(full) == []


# ---- C-5: scenario D says what to do under a strict rule ----

PAGE_D = "docs/integrate/d-gold-in-warehouse.md"
SCENARIO_PAGES = ("a-one-machine", "b-kubernetes", "c-lake-and-warehouse", "d-gold-in-warehouse", "e-agents")


def d_page_problems(text):
    """What the D page lacks of the plain statement: not built; one scheduled job outside the warehouse; C as the nearest built route; new code otherwise."""
    flat = " ".join(text.split()).lower()
    wanted = {
        "d is not built": "that D is not built",
        "needs one scheduled job outside the warehouse": "that meterline as shipped needs one scheduled job outside the warehouse",
        "connectors and stage 0": "that the job runs the connectors and stage 0 at least",
        "the nearest built route is [scenario c](c-lake-and-warehouse.md)": "the sentence naming C as the nearest built route, linked",
        "if it cannot, the port must also move the connectors, which is new code": "the port that also moves the connectors, as new code",
    }
    return [what for needle, what in wanted.items() if needle not in flat]


def index_problems(index):
    """The scenario pages the docs index does not link."""
    return [p for p in SCENARIO_PAGES if "integrate/%s.md" % p not in index]


def test_the_d_page_says_what_to_do_under_a_strict_rule():
    assert d_page_problems(read(PAGE_D)) == []


def test_the_docs_index_links_every_scenario_page_d_included():
    assert index_problems(read("docs/README.md")) == []


def test_negative_control_a_d_page_that_stays_silent_and_an_index_that_skips_d_are_caught():
    assert len(d_page_problems("# D\n\nChoose it only when no batch may run outside the warehouse.\n")) == 5
    index = "[A](integrate/a-one-machine.md) [B](integrate/b-kubernetes.md) [C](integrate/c-lake-and-warehouse.md) [E](integrate/e-agents.md)"
    assert index_problems(index) == ["d-gold-in-warehouse"]
