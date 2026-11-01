"""Review finding F-45: a release path with provenance, trusted publishing, dependency updates, an audit and an SBOM.

The workflows cannot run here; this test checks what can be checked statically: when they run, what they may do,
that every action is pinned to a commit, and that the written procedure covers every package of the repository.

Run: python3 -m pytest -q tools
"""
import os
import re
import sys

import pytest

yaml = pytest.importorskip("yaml")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiring  # noqa: E402  the places a package must be named in; tools/check_new_connector.py lists them

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = sorted(d for d in os.listdir(os.path.join(ROOT, "packages"))
                  if os.path.exists(os.path.join(ROOT, "packages", d, "pyproject.toml")))
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def workflow(name):
    data = yaml.safe_load(read(".github", "workflows", name))
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 reads the key `on` as true
    return data


def steps(data):
    for job_id, job in data["jobs"].items():
        for step in job.get("steps", []):
            yield job_id, step


def test_release_runs_only_on_a_package_version_tag():
    triggers = workflow("release.yml")["on"]
    assert list(triggers) == ["push"]
    assert triggers["push"] == {"tags": ["meterline*-v[0-9]*"]}


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_every_action_is_pinned_to_a_commit(name):
    uses = [step["uses"] for _, step in steps(workflow(name)) if "uses" in step]
    assert uses and all(SHA_PIN.match(u) for u in uses), [u for u in uses if not SHA_PIN.match(u)]


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_no_expression_is_expanded_inside_a_script(name):
    # event data reaches a script through env only, never by ${{ }} inside run: (script injection)
    scripts = [(job, step.get("name")) for job, step in steps(workflow(name)) if "${{" in step.get("run", "")]
    assert scripts == []


def test_release_permissions_are_least_privilege_and_use_no_stored_token():
    data = workflow("release.yml")
    assert data["permissions"] == {}
    granted = {job_id: job.get("permissions", {}) for job_id, job in data["jobs"].items()}
    assert granted["build"] == {"contents": "read", "id-token": "write", "attestations": "write"}
    assert granted["publish"] == {"id-token": "write"}
    assert granted["github-release"] == {"contents": "write"}
    assert data["jobs"]["publish"]["environment"]["name"] == "pypi"
    assert "secrets." not in read(".github", "workflows", "release.yml")
    publish = [s for _, s in steps(data) if s.get("uses", "").startswith("pypa/gh-action-pypi-publish@")]
    assert len(publish) == 1 and "password" not in publish[0].get("with", {})


def test_release_builds_checks_attests_and_records_an_sbom():
    data = workflow("release.yml")
    build = "\n".join(s.get("run", "") + str(s.get("uses", "")) for s in data["jobs"]["build"]["steps"])
    assert "python -m build" in build
    assert "twine check --strict" in build
    assert "actions/attest-build-provenance@" in build
    assert "cyclonedx-py environment" in build
    release = "\n".join(s.get("run", "") for s in data["jobs"]["github-release"]["steps"])
    assert "gh release create" in release and "sbom/" in release
    assert data["jobs"]["publish"]["needs"] == "build"
    assert data["jobs"]["github-release"]["needs"] == ["build", "publish"]


def test_dependabot_watches_the_actions_and_every_package():
    data = yaml.safe_load(read(".github", "dependabot.yml"))
    assert data["version"] == 2
    by_ecosystem = {u["package-ecosystem"]: u for u in data["updates"]}
    assert by_ecosystem["github-actions"]["directory"] == "/"
    assert sorted(by_ecosystem["pip"]["directories"]) == sorted("/packages/" + p for p in PACKAGES)
    assert [p for p in PACKAGES if wiring.dependabot(ROOT, p)] == []  # the same predicate as tools/check_new_connector.py


def test_ci_audits_the_dependencies():
    data = workflow("ci.yml")
    audit = "\n".join(s.get("run", "") for s in data["jobs"]["audit"]["steps"])
    assert re.search(r'"pip-audit==[0-9.]+"', audit)
    assert "pip_audit --skip-editable" in audit
    for package in PACKAGES:
        assert "packages/" + package in audit
    assert "audit" in wiring.ci_install_jobs(ROOT)
    # every install block of the workflow (the tests, the minimum versions and this audit), not the audit alone
    assert {p: wiring.ci(ROOT, p) for p in PACKAGES if wiring.ci(ROOT, p)} == {}


def test_releasing_md_has_the_publishing_procedure_for_every_package():
    text = read("docs", "dev", "RELEASING.md")
    section = text[text.index("\n## Publishing to PyPI\n"):]
    section = section[:section.find("\n## ", 1)] if "\n## " in section[1:] else section
    for package in PACKAGES:  # a package is named in the procedure, or the procedure says in one line that it is not released yet
        assert "`%s`" % package in section or wiring.NOT_RELEASED % package in section, package
    for phrase in ("trusted publisher", "twine check --strict", "attestation", "SBOM", "meterline-anthropic-v0.2.0"):
        assert phrase in section, phrase


# ---- B2-b: every released package has a release path ----

def test_release_yml_accepts_exactly_the_packages_of_the_repository():
    # every package except those the procedure marks "not released yet": a marked package is not in the list
    released, held = wiring.released_packages(ROOT), wiring.not_released_packages(ROOT)
    assert wiring.release_case(ROOT) == released
    assert sorted(released + held) == PACKAGES


def test_releasing_md_names_every_released_package_in_the_reservation_step_and_the_release_order():
    section, reservation, order = wiring.releasing_parts(ROOT)
    assert reservation and order, "the reservation step (1. **Reserve ...) or the release order (### Each release) is missing"
    for package in wiring.released_packages(ROOT):
        assert "`%s`" % package in reservation, "the reservation step does not name %s" % package
        assert "`%s`" % package in order, "the release order does not name %s" % package
    for package in wiring.not_released_packages(ROOT):
        assert "`%s`" % package not in order, "%s is marked not released yet but is in the release order" % package
    assert [p for p in PACKAGES if wiring.release_problems(ROOT, p)] == []  # the same predicate as tools/check_new_connector.py
    assert "not in this procedure yet" not in section


def test_the_admin_package_is_released_with_the_others_after_its_live_run():
    section = read("docs", "dev", "RELEASING.md")
    para = [p for p in section.split("\n\n") if "meterline-anthropic-admin" in p and "live run" in p]
    assert para, "no checklist line names the admin package and its live run"
    flat = " ".join(para[0].split()).lower()
    assert "released in the first release with the others" in flat and "small console organisation" in flat
    assert all("skip its tag" not in " ".join(p.split()).lower() for p in section.split("\n\n"))


def test_the_changelog_has_a_version_entry_template():
    section = read("docs", "dev", "RELEASING.md").split("\n## Publishing to PyPI\n", 1)[1]
    template = section.split("CHANGELOG entry", 1)[1]
    for part in ("## [", "### Added", "### Changed", "### Fixed", "### Known limits"):
        assert part in template, part
