"""The places a package must be named in, as data: one module for tools/check_new_connector.py and for the tools tests.

A new package (a connector made by tools/new_connector.py, or any other) has to appear in a number of files: the README
tables, the front-door files, the CI install blocks, dependabot, the release path. Each such place is an ``Item``: an id,
the file, a predicate ``missing(root, package)`` that returns what is missing (an empty list when the place is wired), the
words ``add(package)`` that the command prints, and the tools tests that check the same place. The tests that check a
place call the item's predicate, so the command and the test cannot disagree. The predicates take ``root``, the
checkout to look at, so a test can point them at a scratch tree.

``NOT_WIRING`` lists the tools tests that read the package list and are not a wiring check, each with its reason.
tools/test_check_new_connector.py reads every tools/test_*.py and fails for a test that reads the package list and is in
neither place: a new tools check for packages cannot be added without the command knowing it.

Add an item here when a new tools test checks a new place; the command then lists it for every future package.
"""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import connectors, in_working_repo, metadata, packages  # noqa: E402

PRIVATE_ONLY = "private repository only - skipped here"
# The release decision, in the words tools/test_release_path.py expects: a package with this line in the section
# "Publishing to PyPI" of docs/dev/RELEASING.md is left out of release.yml's package list and of the release order.
NOT_RELEASED = "`%s` is not released yet."
RELEASING = "docs/dev/RELEASING.md"
RELEASE_YML = ".github/workflows/release.yml"
CI_YML = ".github/workflows/ci.yml"
DEV_INDEX = "docs/dev/README.md"


def mentioned(text, package):
    """True when ``package`` is named in ``text`` as a whole name: meterline-anthropic is not meterline-anthropic-admin."""
    return re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(package), text) is not None


def token(path):
    """A regex for ``path`` as a whole path: packages/meterline-anthropic does not match packages/meterline-anthropic-admin."""
    return re.compile(r"%s(?![\w-])" % re.escape(path))


def read(root, rel):
    """The text of ``rel`` under ``root``, or None when the file is not there."""
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def md_section(text, heading):
    """The body of the first section whose heading (level 2 or 3) starts with ``heading``, or None."""
    match = re.search(r"^#{2,3} %s\b.*?$(.*?)(?=^#{2,3} |\Z)" % re.escape(heading), text, re.M | re.S)
    return match.group(1) if match else None


class Package:
    """What the printed lines are built from: the name, a title, the environment variables and the extras of one package."""

    def __init__(self, root, dist):
        self.root, self.dist = root, dist
        self.name = dist[len("meterline-"):] if dist.startswith("meterline-") else dist
        self.title = self.name.replace("-", " ").title()
        self.exists = os.path.exists(os.path.join(root, "packages", dist, "pyproject.toml"))
        self.connector = self.exists and dist in connectors(root)
        self.extras = sorted(metadata(dist, root).get("optional-dependencies", {})) if self.exists else ["dev", "parquet"]

    @property
    def declared_env_vars(self):
        """The key and token variables the command line reads (``KEY_ENV = "ACME_API_KEY"`` in cli.py)."""
        found = []
        for cli in sorted((Path(self.root) / "packages" / self.dist / "src").glob("*/cli.py")):
            found += re.findall(r'^[A-Z_]*ENV = "([A-Z][A-Z0-9_]*(?:KEY|TOKEN))"', cli.read_text(encoding="utf-8"), re.M)
        return found

    @property
    def env_vars(self):
        """The same, or the name the template gives when the package is not there yet."""
        return self.declared_env_vars or [self.name.replace("-", "_").upper() + "_API_KEY"]

    def extras_for(self, wanted=("dev", "parquet")):
        """``[dev,parquet]``: the extras of this package among ``wanted``, as an install option."""
        have = [e for e in wanted if e in self.extras]
        return "[%s]" % ",".join(have) if have else ""

    @property
    def blurb(self):
        return "A connector for the %s API, with a `%s` command." % (self.title, self.dist) if self.connector else "TODO: what the package holds."

    @property
    def network(self):
        return "`httpx`" if self.connector else "none"


@dataclass(frozen=True)
class Item:
    id: str
    file: str                                  # what to edit, relative to the repository root
    title: str                                 # one sentence: what the place is
    missing: Callable                          # (root, dist) -> list of what is missing; empty when the place is wired
    add: Callable                              # (Package) -> the exact line or table row to add, as text
    tests: Tuple[str, ...] = ()                # 'tools/test_x.py::test_name' of the tools tests that check this place
    connector_only: bool = False               # only a package that calls a provider's API (depends on httpx) has this place
    private_only: bool = False                 # lives in a file the public tree does not ship (docs/dev, .github/dependabot.yml): skipped when the file is absent
    listed_when_unwired: bool = True           # False when a package made by tools/new_connector.py already satisfies it


def applies(item, package):
    return package.connector or not item.connector_only


def skipped(item, root):
    return item.private_only and not os.path.exists(os.path.join(root, item.file))


# ---- predicates ----

def _file_text(root, rel):
    text = read(root, rel)
    return text, ([] if text is not None else ["%s is not there" % rel])


def _names(rel):
    """The place is a file that must name the package as a whole name."""
    def missing(root, dist):
        text, problems = _file_text(root, rel)
        return problems or ([] if mentioned(text, dist) else ["%s does not name %s" % (rel, dist)])
    return missing


def _backticked(rel):
    """The place is a file that must name the package between backticks."""
    def missing(root, dist):
        text, problems = _file_text(root, rel)
        return problems or ([] if "`%s`" % dist in text else ["%s does not name `%s`" % (rel, dist)])
    return missing


def _in_section(rel, heading, pattern, what=None):
    """The place is a section of a file; ``pattern(dist)`` is a regex the section must match, ``what(dist)`` says in words what it looks for.
    ``{}`` in ``rel`` stands for the package."""
    def missing(root, dist):
        rel_path = rel.format(dist)
        text, problems = _file_text(root, rel_path)
        if problems:
            return problems
        body = md_section(text, heading)
        if body is None:
            return ['%s has no section "%s"' % (rel_path, heading)]
        return [] if re.search(pattern(dist), body) else ['the section "%s" of %s has no %s' % (heading, rel_path, what(dist) if what else dist)]
    return missing


def tracked(root, dist):
    """The files of the package folder are tracked by git: the docs and tree tests read ``git ls-files``."""
    if not os.path.exists(os.path.join(root, ".git")):
        return []  # an exported tree has no history; every file in it counts
    folder = "packages/%s" % dist
    try:
        listed = subprocess.run(["git", "-C", root, "ls-files", "-z", "--", folder], capture_output=True, text=True, check=True).stdout
        loose = subprocess.run(["git", "-C", root, "ls-files", "-z", "--others", "--exclude-standard", "--", folder],
                               capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []  # no git, or not a checkout: nothing to say
    if not [x for x in listed.split("\0") if x]:
        return ["no file of %s is tracked by git" % folder]
    untracked = [x for x in loose.split("\0") if x]
    return ["%d file(s) of %s are not tracked, for example %s" % (len(untracked), folder, untracked[0])] if untracked else []


def installed(root, dist):
    """The package imports, and from this checkout (an editable install of another checkout or an old one is no install)."""
    src = os.path.join(root, "packages", dist, "src")
    modules = sorted(d for d in os.listdir(src) if os.path.isfile(os.path.join(src, d, "__init__.py"))) if os.path.isdir(src) else []
    if not modules:
        return ["packages/%s/src holds no import package" % dist]
    spec = importlib.util.find_spec(modules[0])
    if spec is None:
        return ["%s cannot be imported: the package is not installed in this Python" % modules[0]]
    origin = os.path.realpath(spec.origin or "")
    if not origin.startswith(os.path.realpath(src) + os.sep):
        return ["%s is imported from %s, not from this checkout" % (modules[0], os.path.dirname(origin))]
    return []


def quick_start(root, dist):
    text, problems = _file_text(root, "README.md")
    if problems:
        return problems
    import readme_block
    try:
        block = readme_block.block_after(text, "Quick start")
    except SystemExit as error:
        return ["README.md: %s" % error]
    pip = [x for x in block.splitlines() if "pip install" in x and "-e" in x]
    if len(pip) != 1:
        return ["the Quick start of README.md has %d `pip install -e` lines, not one" % len(pip)]
    return [] if token("packages/%s" % dist).search(pip[0]) else ["the install line of the Quick start does not name packages/%s" % dist]


def repo_map(root, dist):
    text, problems = _file_text(root, "README.md")
    if problems:
        return problems
    if "## Repo map" not in text:
        return ['README.md has no section "Repo map"']
    section = text.split("## Repo map", 1)[1].split("\n## ", 1)[0]
    block = re.search(r"```\n(.*?)```", section, re.S)
    listed = [x.split()[0] for x in block.group(1).splitlines() if x and not x.startswith(" ")] if block else []
    return [] if "packages/%s/" % dist in listed else ["the Repo map of README.md does not list packages/%s/" % dist]


def security(root, dist):
    text, problems = _file_text(root, "SECURITY.md")
    if problems:
        return problems
    out = []
    table = md_section(text, "Supported versions")
    if table is None or "`%s`" % dist not in table:
        out.append('the table "Supported versions" of SECURITY.md does not name `%s`' % dist)
    package = Package(root, dist)
    scope = md_section(text, "Scope") or ""
    if package.connector:
        out += ['the section "Scope" of SECURITY.md does not name %s' % var for var in package.declared_env_vars if var not in scope]
    return out


def ci_install_jobs(root):
    """{job id: text} of the jobs of ci.yml that install every package in editable mode: the ones with the core install line."""
    text = read(root, CI_YML) or ""
    jobs = re.split(r"^  ([\w-]+):\n", text.split("\njobs:\n", 1)[-1], flags=re.M)
    return {jobs[i]: jobs[i + 1] for i in range(1, len(jobs) - 1, 2) if '-e "packages/meterline[' in jobs[i + 1]}


def ci(root, dist):
    if read(root, CI_YML) is None:
        return ["%s is not there" % CI_YML]
    jobs = ci_install_jobs(root)
    if not jobs:
        return ["%s has no install block (a line with -e \"packages/meterline[...)" % CI_YML]
    pattern = re.compile(r'-e "packages/%s[\["]' % re.escape(dist))
    return ["the install block of job `%s` in %s does not install packages/%s" % (job, CI_YML, dist)
            for job, body in jobs.items() if not pattern.search(body)]


def dependabot_directories(root):
    """The folders in the `directories:` list of the pip entry of dependabot.yml, wherever the entry sits and whatever keys
    (schedule, groups, commit-message) come around the list."""
    entries = re.split(r"^  - (?=package-ecosystem:)", read(root, ".github/dependabot.yml") or "", flags=re.M)
    for entry in entries:
        if re.match(r"package-ecosystem: pip\s*$", entry, re.M):
            listed = re.search(r"^    directories:\s*\n((?:      - .*\n?)+)", entry, re.M)
            return re.findall(r'"/(packages/[\w-]+)"', listed.group(1)) if listed else []
    return []


def dependabot(root, dist):
    if read(root, ".github/dependabot.yml") is None:
        # the export leaves dependabot.yml out of the public tree: nothing to wire there; in the working repository a missing file is a problem
        return [] if not in_working_repo(root) else [".github/dependabot.yml is not there"]
    return [] if "packages/%s" % dist in dependabot_directories(root) else ['the pip entry of .github/dependabot.yml does not list "/packages/%s"' % dist]


def release_case(root):
    """The package names release.yml accepts in its `case "$package" in` list, sorted; None when the file or the list is not there."""
    match = re.search(r'case "\$package" in\n\s*([^\n)]+)\) ;;', read(root, RELEASE_YML) or "")
    return sorted(match.group(1).split("|")) if match else None


def releasing_parts(root):
    """(section, reservation step, release order) of docs/dev/RELEASING.md, or None when the file or its section is not there."""
    text = read(root, RELEASING)
    if text is None or "\n## Publishing to PyPI\n" not in text:
        return None
    section = text.split("\n## Publishing to PyPI\n", 1)[1]
    section = section.split("\n## ", 1)[0]
    reservation = section.split("1. **Reserve", 1)[1].split("\n2. ", 1)[0] if "1. **Reserve" in section else ""
    order = section.split("### Each release", 1)[1].split("3. Tag the merged commit", 1)[0] if "### Each release" in section else ""
    return section, reservation, order


def not_released_packages(root):
    """The packages whose RELEASING.md section carries the line NOT_RELEASED."""
    parts = releasing_parts(root)
    return [p for p in packages(root) if parts and NOT_RELEASED % p in parts[0]]


def released_packages(root):
    gone = set(not_released_packages(root))
    return [p for p in packages(root) if p not in gone]


def release_problems(root, dist):
    """What is wrong with the release decision of ``dist``: it is released (in the case list, the reservation step and the release
    order) or it carries the NOT_RELEASED line (and is in neither the case list nor the release order)."""
    case, parts = release_case(root), releasing_parts(root)
    out = []
    if case is None:
        out.append("%s has no package list (`case \"$package\" in`)" % RELEASE_YML)
    if parts is None:
        if in_working_repo(root) or os.path.exists(os.path.join(root, RELEASING)):
            out.append('%s has no section "Publishing to PyPI"' % RELEASING)
        parts = ("", "", "")  # the public tree does not ship RELEASING.md; the workflow list is still checked
        judged = False
    else:
        judged = True
    section, reservation, order = parts
    listed_in_case = case is not None and dist in case
    if judged and NOT_RELEASED % dist in section:
        if listed_in_case:
            out.append("%s lists %s, which %s says is not released yet" % (RELEASE_YML, dist, RELEASING))
        if "`%s`" % dist in order:
            out.append("the release order in %s names %s, which it says is not released yet" % (RELEASING, dist))
        return out
    if case is not None and not listed_in_case:
        out.append("%s does not accept %s" % (RELEASE_YML, dist))
    if judged:
        if "`%s`" % dist not in reservation:
            out.append("the reservation step of %s does not name %s" % (RELEASING, dist))
        if "`%s`" % dist not in order:
            out.append("the release order of %s does not name %s" % (RELEASING, dist))
    return out


def counts(root, dist):
    """Every number of connectors or packages written in a public document that differs from the tree (one scan, test_counts_match_the_tree)."""
    try:
        import test_counts_match_the_tree as scan
    except ImportError as error:  # the scan lives in a test module: pytest must be installed
        return ["cannot run the count scan (%s): pip install -e \"packages/%s[dev]\"" % (error, dist)]
    with tempfile.TemporaryDirectory() as tmp:
        return scan.counts_that_disagree(Path(tmp), root)


# ---- the words to print ----

def _row(package):
    return "| [`packages/%s`](packages/%s/README.md) | %s | %s |" % (package.dist, package.dist, package.blurb, package.network)


def _map_line(package):
    kind = "connector" if package.connector else "package"
    return ("packages/%s/" % package.dist).ljust(36) + "%s %s, CLI, tests (fake API, synthetic rows)" % (package.title, kind)


def _installed_add(package):
    first = '-e "packages/meterline[conformance]" ' if package.dist != "meterline" else ""
    return '.venv/bin/python -m pip install %s-e "packages/%s%s"' % (first, package.dist, package.extras_for(("dev",)))


def _quick_start_add(package):
    return 'add  -e "packages/%s%s"  to the one `pip install -e` line, after the other connectors' % (package.dist, package.extras_for(("dev",)))


def _front_door_add(package):
    return "- `%s`: built against documented shapes." % package.dist


def _agents_add(package):
    return ("under \"Evidence level\", add the line:\n    %s\n  and name it where the connectors are listed (the install line and the commands list)"
            % _front_door_add(package))


def _llms_add(package):
    base = "https://github.com/OWNER/REPO/blob/main/"
    existing = re.search(r"\((https://[^)\s]*/blob/main/)packages/meterline-", read(package.root, "llms.txt") or "")
    base = existing.group(1) if existing else base
    return ("under the package links of \"Docs\", add the line:\n    - [%s](%spackages/%s/README.md): the %s API.\n"
            "  and, with the evidence levels at the top (plain lines, no dash), the line:\n    `%s`: built against documented shapes."
            % (package.dist, base, package.dist, package.title, package.dist))


def _threat_add(package):
    return ('in the "Covers" bullet at the top, add `%s` to the connectors; if it crosses a trust boundary the table does not have, '
            "add the row and its threats" % package.dist)


def _security_add(package):
    envs = ", ".join("`%s`" % v for v in package.env_vars)
    return ('table "Supported versions": add `%s` to the packages of the row "the latest release"\n'
            '  section "Scope": add %s to the list of keys and tokens' % (package.dist, envs))


def _changelog_add(package):
    return "under `## [Unreleased]`, `### Added`, add the line:\n    - `%s`: a connector for the %s API." % (package.dist, package.title)


def _docs_index_add(package):
    return ('section "Run a connector on a real account", add the line:\n    - [%s](../packages/%s/README.md): the %s API.'
            % (package.title, package.dist, package.title))


def _operate_add(package):
    cli = next(iter(sorted((Path(package.root) / "packages" / package.dist / "src").glob("*/cli.py"))), None)
    found = re.search(r'--rate-per-(minute|hour)"[^\n]*default=(\d+)', cli.read_text(encoding="utf-8")) if cli and cli.exists() else None
    rate = "%s a %s" % (found.group(2), found.group(1)) if found else "<default rate>"
    return ('table of "How long a first backfill takes", add the row (an estimate from the documented limits and window sizes, not a measurement):\n'
            "    | `%s` | %s | <requests for a year of history, and how they are counted> | <time at the default rate> |" % (package.dist, rate))


def _known_limits_add(package):
    return ("keep the section \"Known limits\" of the package README that the template wrote: it says what `pending` in `state.json` means;\n"
            "  a README without the literal `pending` in that section fails")


def _ci_add(package):
    jobs = ", ".join(ci_install_jobs(package.root)) or "tests, minimum-versions, audit"
    return ('in every install block (jobs: %s), after the other connectors, add the line:\n    -e "packages/%s%s" \\'
            % (jobs, package.dist, package.extras_for()))


def _dependabot_add(package):
    return ('in the `directories:` list of the `package-ecosystem: pip` entry (one more item; leave `groups:` and the rest as they are), add the line:\n'
            '      - "/packages/%s"' % package.dist)


def _release_add(package):
    return (
        "decide, and do (a) or (b):\n"
        "  (a) released:\n"
        "      %s: add %s to the `case \"$package\" in` list (names separated by `|`)\n"
        "      %s, \"Publishing to PyPI\": name `%s` in step 1, the reservation of the names, and in the release order of \"Each release\"\n"
        "  (b) not released yet:\n"
        "      %s, in the section \"Publishing to PyPI\", add the line:\n"
        "          %s\n"
        "      and leave it out of the list of release.yml and out of the release order"
        % (RELEASE_YML, package.dist, RELEASING, package.dist, RELEASING, NOT_RELEASED % package.dist))


def _dev_index_add(package):
    return ("table \"The spec in force per package\": add the row (replace the two cells when the package has a spec and a change list):\n"
            "    | `%s` | no spec yet | - |" % package.dist)


def _counts_add(package):
    return "reword each phrase listed below so that it names no number of connectors or packages (write \"the connectors\" or name them)"


def _git_add(package):
    return "git add packages/%s" % package.dist


ITEMS = (
    Item("git-tracked", "packages/<package>", "The files of the package are tracked by git (the docs and tree tests read `git ls-files`).",
         tracked, _git_add, tests=("tools/test_release_hygiene.py::test_no_private_working_trace_in_the_exported_tree",
                                   "tools/test_release_hygiene.py::test_no_file_name_carries_a_ticket_id",
                                   "tools/test_docs_structure.py::test_no_broken_relative_link_in_a_markdown_file")),
    Item("installed", "packages/<package>", "The package is installed, editable, from this checkout (the console-script test imports it).",
         installed, _installed_add, tests=("tools/test_console_scripts.py::test_console_script_target_is_the_packages_own_function",)),
    Item("readme-packages-table", "README.md", 'The row of the "Packages" table.',
         _in_section("README.md", "Packages", lambda d: r"\(packages/%s/README\.md\)" % re.escape(d),
                     lambda d: "link to packages/%s/README.md" % d), _row,
         tests=("tools/test_connector_contract_docs.py::test_readme_agents_llms_and_threat_model_name_every_connector",)),
    Item("readme-quick-start", "README.md", 'The install line of the "Quick start".', quick_start, _quick_start_add,
         tests=("tools/test_release_hygiene.py::test_quick_start_installs_every_package_and_runs_the_tests",)),
    Item("readme-repo-map", "README.md", 'The line of the "Repo map".', repo_map, _map_line,
         tests=("tools/test_release_hygiene.py::test_readme_repo_map_lists_only_files_the_export_ships",)),
    Item("agents-md", "AGENTS.md", "The connector is named for coding agents.", _names("AGENTS.md"), _agents_add,
         tests=("tools/test_connector_contract_docs.py::test_readme_agents_llms_and_threat_model_name_every_connector",), connector_only=True),
    Item("llms-txt", "llms.txt", "The connector is named for language models.", _names("llms.txt"), _llms_add,
         tests=("tools/test_connector_contract_docs.py::test_readme_agents_llms_and_threat_model_name_every_connector",), connector_only=True),
    Item("threat-model", "docs/THREAT-MODEL.md", "The connector is among those the threat model covers.", _names("docs/THREAT-MODEL.md"), _threat_add,
         tests=("tools/test_connector_contract_docs.py::test_readme_agents_llms_and_threat_model_name_every_connector",), connector_only=True),
    Item("security-md", "SECURITY.md", "The package has a supported-versions row and its keys are in the scope.", security, _security_add,
         tests=("tools/test_security_docs.py::test_security_policy_names_both_keys_and_every_package",)),
    Item("changelog", "CHANGELOG.md", "The package has an entry.", _backticked("CHANGELOG.md"), _changelog_add,
         tests=("tools/test_release_hygiene.py::test_changelog_follows_keep_a_changelog_and_covers_every_package",)),
    Item("docs-index", "docs/README.md", 'The link under "Run a connector on a real account".',
         _in_section("docs/README.md", "Run a connector on a real account", lambda d: r"packages/%s/README\.md" % re.escape(d),
                     lambda d: "link to packages/%s/README.md" % d), _docs_index_add,
         connector_only=True),
    Item("operate-daily", "docs/how-to/operate-daily.md", "The connector has a row in the duration table.",
         _in_section("docs/how-to/operate-daily.md", "How long a first backfill takes", lambda d: "`%s`" % re.escape(d),
                     lambda d: "row for `%s`" % d), _operate_add,
         tests=("tools/test_operate_daily.py::test_the_duration_table_names_every_connector",), connector_only=True),
    Item("known-limits-pending", "packages/<package>/README.md", 'The "Known limits" section says what `pending` means.',
         _in_section("packages/{}/README.md", "Known limits", lambda d: "`pending`",
                     lambda d: "literal `pending` (what `pending` in state.json means)"), _known_limits_add,
         tests=("tools/test_review_f18_known_limits.py::test_left_open_findings_are_in_a_limits_section",), connector_only=True,
         listed_when_unwired=False),
    Item("ci", CI_YML, "The package is installed by every install block of the CI workflow.", ci, _ci_add,
         tests=("tools/test_release_path.py::test_ci_audits_the_dependencies",)),
    Item("dependabot", ".github/dependabot.yml", "Dependabot watches the package.", dependabot, _dependabot_add,
         tests=("tools/test_release_path.py::test_dependabot_watches_the_actions_and_every_package",), private_only=True),
    Item("release-decision", "%s and %s" % (RELEASE_YML, RELEASING), "The package is released, or the procedure says it is not released yet.",
         release_problems, _release_add,
         tests=("tools/test_release_path.py::test_release_yml_accepts_exactly_the_packages_of_the_repository",
                "tools/test_release_path.py::test_releasing_md_has_the_publishing_procedure_for_every_package",
                "tools/test_release_path.py::test_releasing_md_names_every_released_package_in_the_reservation_step_and_the_release_order")),
    Item("docs-dev-readme", DEV_INDEX, "The package is in the table of specs in force (a maintainer file: the public tree has none).",
         _backticked(DEV_INDEX), _dev_index_add,
         tests=("tools/test_docs_structure.py::test_docs_dev_names_the_spec_in_force_per_package",), private_only=True),
    Item("counts", "the documents listed", "No document writes a number of connectors or packages.", counts, _counts_add,
         tests=("tools/test_counts_match_the_tree.py::test_counts_match_the_tree",), listed_when_unwired=False),
)


BY_ID = {item.id: item for item in ITEMS}


# ---- the tools tests that read the package list and check no place a package must appear in ----

def _excuse(reason, *tests):
    return {"tools/%s" % t: reason for t in tests}


NOT_WIRING = {
    **_excuse("checks the package files the template writes (README outline, links, build); a package made by new_connector.py passes them",
              "test_package_readmes.py::test_build_then_twine_check_passes",
              "test_package_readmes.py::test_every_link_into_the_repository_names_an_existing_path",
              "test_package_readmes.py::test_first_screen_has_purpose_an_install_line_and_something_to_run",
              "test_package_readmes.py::test_links_into_the_repository_are_built_from_the_homepage",
              "test_package_readmes.py::test_no_relative_link_in_a_package_readme",
              "test_package_readmes.py::test_the_clone_form_comes_after_the_index_form_under_from_a_clone",
              "test_package_readmes.py::test_the_first_install_line_installs_the_package_from_the_index",
              "test_review_f24_prose.py::test_every_table_and_list_has_a_sentence_before_it",
              "test_review_f24_prose.py::test_known_limits_are_grouped_by_kind",
              "test_review_f24_prose.py::test_no_sentence_is_longer_than_45_words",
              "test_review_f24_prose.py::test_root_known_limits_copies_no_bullet_of_a_package_readme",
              "test_review_f24_prose.py::test_the_live_run_caveat_is_stated_once_per_file",
              "test_review_f25_provider_docs.py::test_connector_readmes_follow_one_outline",
              "test_review_f25_provider_docs.py::test_every_connector_says_what_to_do_when_a_live_response_differs",
              "test_review_f32_front_door.py::test_every_package_readme_carries_the_same_purpose_sentence",
              "test_review_f12_evidence.py::test_every_connector_status_names_its_level_and_links_the_definition",
              "test_review_f12_evidence.py::test_no_unbacked_independent_check_claim",
              "test_review_f18_known_limits.py::test_every_package_has_known_limits_linked_from_its_status",
              "test_release_hygiene.py::test_every_package_declares_the_licence_and_ships_it",
              "test_release_hygiene.py::test_every_package_folder_is_found_and_whole"),
    **_excuse("checks the pyproject.toml the template writes (metadata, bounds, extras, imports); a package made by new_connector.py passes them",
              "test_packaging_metadata.py::test_a_package_at_1_0_or_later_is_not_alpha",
              "test_packaging_metadata.py::test_a_test_that_needs_an_extra_the_base_install_lacks_skips_instead_of_erroring",
              "test_packaging_metadata.py::test_dependents_require_the_meterline_that_has_the_names_they_import",
              "test_packaging_metadata.py::test_dev_extra_has_xdist_and_ruff",
              "test_packaging_metadata.py::test_every_dependency_has_a_lower_bound",
              "test_packaging_metadata.py::test_every_third_party_import_is_declared",
              "test_packaging_metadata.py::test_metadata_is_complete",
              "test_packaging_metadata.py::test_python_classifiers_and_minimum_match_the_ci_matrix",
              "test_packaging_metadata.py::test_records_md_names_no_package_at_1_0_while_none_is",
              "test_console_scripts.py::test_every_package_declares_a_command"),
    **_excuse("checks the source the template writes (lint, length, suppressions, retired names); test_new_connector.py lints a generated package",
              "test_lint.py::test_no_function_in_the_package_sources_is_longer_than_100_logical_lines",
              "test_lint.py::test_package_sources_pass_the_committed_ruff_config",
              "test_security_privacy_docs.py::test_every_security_suppression_states_its_reason",
              "test_security_privacy_docs.py::test_security_lint_of_package_sources_is_clean_or_justified",
              "test_no_retired_public_names.py::test_no_retired_public_names_in_the_schemas"),
    **_excuse("a negative control or a self-test of a check: it builds its input from the package list and checks no place",
              "test_connector_contract_docs.py::test_there_are_connectors_to_check",
              "test_connector_contract_docs.py::test_no_tools_test_hard_codes_the_package_list",
              "test_connector_contract_docs.py::test_the_hard_coded_list_check_flags_one",
              "test_counts_match_the_tree.py::test_the_scan_flags_a_wrong_count_and_passes_a_right_one",
              "test_counts_match_the_tree.py::test_the_two_ways_to_find_the_connectors_agree"),
}
