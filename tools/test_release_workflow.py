"""C-1 and C-2 of the v0.3 hardening spec: the release job attests first and runs nothing else with the token.

The workflow cannot run here (it starts only on a tag of the public repository), so this test parses it. In the job that
holds ``id-token: write`` and ``attestations: write``, the steps before the attest step are the checkout, the install of the
build tools from a hashed constraints file and the build; the step after it uploads the files. Everything else that runs
third-party code (``twine``, the install of the built wheel, the SBOM tool) runs in a second job that holds neither
permission and first checks that each file's sha256 is the one that was attested.

Run: python3 -m pytest -q tools
"""
import os
import re

import pytest
try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = sorted(d for d in os.listdir(os.path.join(ROOT, "packages"))
                  if os.path.exists(os.path.join(ROOT, "packages", d, "pyproject.toml")))
CONSTRAINTS = os.path.join(ROOT, "tools", "release-constraints.txt")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def jobs():
    return yaml.safe_load(read(".github", "workflows", "release.yml"))["jobs"]


def privileged_job():
    """The one job that can attest: it holds id-token and attestations, both write."""
    found = [(name, job) for name, job in jobs().items()
             if (job.get("permissions") or {}).get("id-token") == "write" and (job.get("permissions") or {}).get("attestations") == "write"]
    assert len(found) == 1, [name for name, _ in found]
    return found[0]


def kind(step):
    uses, run = step.get("uses", ""), step.get("run", "")
    if uses.startswith("actions/checkout@"):
        return "checkout"
    if uses.startswith("actions/attest-build-provenance@"):
        return "attest"
    if uses.startswith("actions/upload-artifact@"):
        return "upload"
    if "pip install" in run and "-m build" not in run:
        return "install"
    if "-m build" in run:
        return "build"
    return "other"


def test_the_privileged_job_runs_only_checkout_install_and_build_before_it_attests():
    _, job = privileged_job()
    kinds = [kind(s) for s in job["steps"]]
    assert kinds.count("attest") == 1, kinds
    at = kinds.index("attest")
    assert kinds[:at] == ["checkout", "install", "build"], [(s.get("name"), k) for s, k in zip(job["steps"], kinds)][:at]
    assert set(kinds[at + 1:]) == {"upload"}, kinds[at + 1:]


def test_the_privileged_job_installs_only_with_hashes_from_the_constraints_file():
    _, job = privileged_job()
    installs = [s["run"] for s in job["steps"] if "pip install" in s.get("run", "")]
    assert installs
    for run in installs:
        assert "--require-hashes" in run, run
        assert "-r tools/release-constraints.txt" in run, run
        assert "--only-binary" in run, run  # no source build runs code while the token is there


def test_the_build_does_not_fetch_its_own_tools_unhashed():
    _, job = privileged_job()
    build = [s["run"] for s in job["steps"] if "-m build" in s.get("run", "")]
    assert build and all("--no-isolation" in run for run in build), build  # build isolation would install setuptools unhashed


def test_the_second_job_has_neither_permission_and_compares_digests_before_it_runs_anything():
    name, _ = privileged_job()
    others = [(n, j) for n, j in jobs().items() if n != name and "check" in j.get("name", "") + n]
    assert len(others) == 1, [n for n, _ in others]
    check_name, check = others[0]
    permissions = check.get("permissions") or {}
    assert "id-token" not in permissions and "attestations" not in permissions, permissions
    assert name in (check["needs"] if isinstance(check["needs"], list) else [check["needs"]])
    runs = [s.get("run", "") for s in check["steps"]]
    compare = [i for i, run in enumerate(runs) if "sha256sum" in run and "-c" in run]
    assert len(compare) == 1, runs
    for i, run in enumerate(runs):
        if re.search(r"twine|pip install|cyclonedx|pip_audit|python -m venv", run):
            assert i > compare[0], "step %d runs a tool before the digests are compared: %s" % (i, run[:60])


def test_the_digests_the_second_job_compares_with_are_the_attested_ones():
    name, job = privileged_job()
    text = yaml.safe_dump(job)
    assert "digests" in job.get("outputs", {}), "the job gives the digests it built (and attests) to the next job"
    build_step = [s for s in job["steps"] if kind(s) == "build"][0]["run"]
    assert "sha256sum" in build_step and "GITHUB_OUTPUT" in build_step, build_step
    assert text.count("actions/attest-build-provenance@") == 1


def test_publish_and_the_github_release_consume_the_attested_artifact_only():
    for job_id in ("publish", "github-release"):
        job = jobs()[job_id]
        needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
        assert "build" in needs and any("check" in n for n in needs), (job_id, needs)
        downloads = [s["with"]["name"] for s in job["steps"] if s.get("uses", "").startswith("actions/download-artifact@")]
        assert downloads and downloads[0] == "dist", (job_id, downloads)
        assert not [s for s in job["steps"] if re.search(r"-m build|setup-python|pip install", s.get("run", "") + s.get("uses", ""))]
        runs = [s.get("run", "") for s in job["steps"]]
        assert any("sha256sum" in run and "-c" in run for run in runs), "%s does not compare digests" % job_id


# ---- C-2: exact, hashed pins ----------------------------------------------------------------------------------------


def constraint_requirements():
    """[(requirement line, [its --hash lines])] of the constraints file; comments and blank lines are ignored."""
    text = read("tools", "release-constraints.txt")
    blocks, current = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash="):
            assert current is not None, line
            current[1].append(line.rstrip(" \\"))
        else:
            current = [line.rstrip(" \\"), []]
            blocks.append(current)
    return blocks


def test_every_line_of_the_constraints_file_is_an_exact_pin_with_a_hash():
    blocks = constraint_requirements()
    names = {re.split(r"==", line)[0].lower() for line, _ in blocks}
    assert {"build", "twine", "setuptools", "wheel", "cyclonedx-bom"} <= names
    for line, hashes in blocks:
        assert re.fullmatch(r"[A-Za-z0-9._-]+==[0-9][A-Za-z0-9.+!_-]*", line), line
        assert hashes and all(re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", h) for h in hashes), line


@pytest.mark.parametrize("package", PACKAGES)
def test_build_system_requires_pins_setuptools_to_the_version_of_the_constraints(package):
    requires = tomllib.loads(read("packages", package, "pyproject.toml"))["build-system"]["requires"]
    assert not [r for r in requires if ">" in r or "<" in r or "~=" in r], requires
    pinned = {line.split("==")[0].lower(): line.split("==")[1] for line, _ in constraint_requirements()}
    assert requires == ["setuptools==" + pinned["setuptools"]], requires
