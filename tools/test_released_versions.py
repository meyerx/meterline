"""A package whose files changed since its last released tag must carry a new version.

A package released as ``NAME-vVERSION`` is immutable on PyPI, so code that changed after that tag and still says VERSION
cannot be released: the tag name is taken, or the new code would go out under an old number. For every package, this
compares the last tag ``NAME-vVERSION`` that HEAD can reach with HEAD, over the files under ``packages/NAME/`` that are
not tests, and fails when any differs while ``pyproject.toml`` still says VERSION. A package with no tag yet is skipped.

The release tags live on the public repository, and the private working repository does not carry them. So the check
also reads the public repository's release tags (``git ls-remote --tags``), fetches them into ``refs/public-tags/``
(never into ``refs/tags/``) and compares the tagged tree with HEAD in the same way. When the public repository cannot be
reached and a package has no local tag either, the check for that package is skipped with a message that says the
network was the reason: it never passes without saying so. ``METERLINE_PUBLIC_URL`` replaces the address, for a mirror.

Run: python3 -m pytest -q tools
"""
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import package_list  # noqa: E402

ROOT = package_list.ROOT
PUBLIC_URL = os.environ.get("METERLINE_PUBLIC_URL", "https://github.com/meyerx/meterline")
PUBLIC_REFS = "refs/public-tags/"
NETWORK_SECONDS = 60


def _git(root, *args):
    return subprocess.run(["git", "--no-optional-locks", *args], cwd=root, capture_output=True, text=True)


def _version_key(version):
    return tuple(int(part) for part in version.split("."))


def _version_pattern(package):
    return re.compile(r"^%s-v(\d+\.\d+\.\d+)$" % re.escape(package))


def public_release_tags(root, url=PUBLIC_URL, names=None):
    """Fetch the release tags of the public repository at ``url`` into ``refs/public-tags/`` of ``root``.

    Returns ``({tag name: ref}, None)`` or ``(None, reason)`` when the repository cannot be read: no network, a
    refused address, a timeout. ``names`` limits the tags to the ones that look like ``<package>-v<version>`` for a
    package of this repository. Nothing is written into ``refs/tags/``."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        listing = subprocess.run(["git", "--no-optional-locks", "ls-remote", "--tags", url], cwd=root, capture_output=True,
                                 text=True, timeout=NETWORK_SECONDS, env=env)
    except (subprocess.TimeoutExpired, OSError) as error:
        return None, "the public repository could not be reached (%s)" % type(error).__name__
    if listing.returncode != 0:
        return None, "the public repository could not be reached (%s)" % (listing.stderr.strip().splitlines() or ["no output"])[-1]
    patterns = [_version_pattern(p) for p in (names if names is not None else package_list.packages(root))]
    wanted = []
    for line in listing.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/tags/") and not parts[1].endswith("^{}"):
            name = parts[1][len("refs/tags/"):]
            if any(p.match(name) for p in patterns):
                wanted.append(name)
    if not wanted:
        return {}, None
    spec = ["+refs/tags/%s:%s%s" % (name, PUBLIC_REFS, name) for name in wanted]
    try:
        fetched = subprocess.run(["git", "--no-optional-locks", "fetch", "--quiet", "--no-tags", url, *spec], cwd=root,
                                 capture_output=True, text=True, timeout=NETWORK_SECONDS * 5, env=env)
    except (subprocess.TimeoutExpired, OSError) as error:
        return None, "the public release tags could not be fetched (%s)" % type(error).__name__
    if fetched.returncode != 0:
        return None, "the public release tags could not be fetched (%s)" % (fetched.stderr.strip().splitlines() or ["no output"])[-1]
    return {name: PUBLIC_REFS + name for name in wanted}, None


def release_refs(root, package, public=None):
    """{VERSION: git ref} of every release of the package: tags that HEAD can reach, then the public repository's.

    ``public`` is the mapping that ``public_release_tags`` returns. A version that both hold is read from the local tag."""
    out = _git(root, "tag", "--merged", "HEAD", "--list", package + "-v*")
    pattern = _version_pattern(package)
    refs = {}
    for name in out.stdout.split():
        match = pattern.match(name)
        if match:
            refs[match.group(1)] = name
    for name, ref in (public or {}).items():
        match = pattern.match(name)
        if match:
            refs.setdefault(match.group(1), ref)
    return refs


def last_released_version(root, package, public=None):
    """The highest VERSION of a tag ``package-vVERSION`` that HEAD can reach or the public repository holds, or None."""
    refs = release_refs(root, package, public)
    return max(refs, key=_version_key) if refs else None


def _is_test_file(path):
    parts = path.split("/")
    name = parts[-1]
    return "tests" in parts[2:-1] or name.startswith("test_") or name == "conftest.py"


def changed_files(root, package, version, public=None):
    """The non-test files under packages/<package>/ that differ between the tag and HEAD."""
    tag = release_refs(root, package, public)[version]
    out = _git(root, "diff", "--name-only", "--no-renames", tag, "HEAD", "--", "packages/%s" % package)
    assert out.returncode == 0, out.stderr
    return [f for f in out.stdout.split("\n") if f and not _is_test_file(f)]


def problem(root, package, public=None):
    """Why the package's version is wrong, or None. None also when the package has no tag yet."""
    released = last_released_version(root, package, public)
    if released is None:
        return None
    changed = changed_files(root, package, released, public)
    declared = package_list.metadata(package, root)["version"]
    if changed and declared == released:
        return "%s changed since %s-v%s (%s ...) but pyproject.toml still says %s" % (package, package, released, changed[0], declared)
    return None


@pytest.fixture(scope="session")
def public_tags():
    """The public repository's release tags, fetched once, or the reason they could not be read."""
    return public_release_tags(ROOT)


@pytest.mark.parametrize("package", package_list.packages())
def test_a_changed_package_carries_a_new_version(package, public_tags):
    tags, reason = public_tags
    if last_released_version(ROOT, package, tags) is None:
        if tags is None:
            pytest.skip("%s: no local release tag, and %s: network unavailable, so the check could not run" % (package, reason))
        pytest.skip("%s has no release tag in this clone or on the public repository" % package)
    assert problem(ROOT, package, tags) is None, problem(ROOT, package, tags)


# ---- negative controls: a planted repository, one case each ----

def _repo(tmp_path, version="0.1.0"):
    def run(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q", "-b", "main")
    run("config", "user.name", "Test")
    run("config", "user.email", "test@example.com")
    run("config", "commit.gpgsign", "false")
    run("config", "tag.gpgsign", "false")
    folder = tmp_path / "packages" / "meterline-acme"
    (folder / "src").mkdir(parents=True)
    (folder / "tests").mkdir()
    (folder / "pyproject.toml").write_text('[project]\nname = "meterline-acme"\nversion = "%s"\n' % version, encoding="utf-8")
    (folder / "src" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    (folder / "tests" / "test_mod.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    run("add", ".")
    run("commit", "-q", "-m", "first")
    run("tag", "-a", "meterline-acme-v" + version, "-m", "meterline-acme " + version)
    return folder, run


def _commit(run, message="change"):
    run("add", ".")
    run("commit", "-q", "-m", message)


def test_a_source_change_with_the_released_version_is_flagged(tmp_path):
    folder, run = _repo(tmp_path)
    (folder / "src" / "mod.py").write_text("X = 2\n", encoding="utf-8")
    _commit(run)
    assert "still says 0.1.0" in problem(tmp_path, "meterline-acme")


def test_a_source_change_with_a_new_version_passes(tmp_path):
    folder, run = _repo(tmp_path)
    (folder / "src" / "mod.py").write_text("X = 2\n", encoding="utf-8")
    (folder / "pyproject.toml").write_text('[project]\nname = "meterline-acme"\nversion = "0.1.1"\n', encoding="utf-8")
    _commit(run)
    assert problem(tmp_path, "meterline-acme") is None


def test_a_change_to_tests_only_passes(tmp_path):
    folder, run = _repo(tmp_path)
    (folder / "tests" / "test_mod.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    _commit(run)
    assert problem(tmp_path, "meterline-acme") is None


def test_an_unchanged_package_passes(tmp_path):
    _repo(tmp_path)
    assert problem(tmp_path, "meterline-acme") is None


def test_a_package_with_no_tag_is_skipped(tmp_path):
    folder, run = _repo(tmp_path)
    run("tag", "-d", "meterline-acme-v0.1.0")
    (folder / "src" / "mod.py").write_text("X = 2\n", encoding="utf-8")
    _commit(run)
    assert last_released_version(tmp_path, "meterline-acme") is None and problem(tmp_path, "meterline-acme") is None


def test_the_highest_reachable_tag_is_the_one_compared(tmp_path):
    folder, run = _repo(tmp_path)
    (folder / "pyproject.toml").write_text('[project]\nname = "meterline-acme"\nversion = "0.1.10"\n', encoding="utf-8")
    _commit(run)
    run("tag", "-a", "meterline-acme-v0.1.10", "-m", "meterline-acme 0.1.10")
    (folder / "src" / "mod.py").write_text("X = 3\n", encoding="utf-8")
    _commit(run)
    assert last_released_version(tmp_path, "meterline-acme") == "0.1.10"  # not "0.1.0" by string order
    assert "still says 0.1.10" in problem(tmp_path, "meterline-acme")


def test_a_package_tag_is_not_taken_for_a_longer_package_name(tmp_path):
    _, run = _repo(tmp_path)
    run("tag", "-a", "meterline-acme-extra-v9.9.9", "-m", "other package")
    assert last_released_version(tmp_path, "meterline-acme") == "0.1.0"


# ---- the release tags that exist only on the public repository ----

def _private_copy(tmp_path):
    """A second repository with the same files and no shared history, as the working repository is to the public one."""
    private = tmp_path / "private"
    private.mkdir()
    folder, run = _repo(private)
    run("tag", "-d", "meterline-acme-v0.1.0")  # the working repository carries no release tag
    return folder, run, private


def test_a_tag_only_the_public_repository_holds_is_compared(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    _repo(public)
    folder, run, private = _private_copy(tmp_path)
    (folder / "src" / "mod.py").write_text("X = 2\n", encoding="utf-8")
    _commit(run)
    assert last_released_version(private, "meterline-acme") is None  # the local view alone sees no release
    tags, reason = public_release_tags(private, url=str(public), names=["meterline-acme"])
    assert reason is None and list(tags) == ["meterline-acme-v0.1.0"]
    assert last_released_version(private, "meterline-acme", tags) == "0.1.0"
    assert "still says 0.1.0" in problem(private, "meterline-acme", tags)


def test_a_public_tag_with_a_new_version_passes(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    _repo(public)
    folder, run, private = _private_copy(tmp_path)
    (folder / "src" / "mod.py").write_text("X = 2\n", encoding="utf-8")
    (folder / "pyproject.toml").write_text('[project]\nname = "meterline-acme"\nversion = "0.2.0"\n', encoding="utf-8")
    _commit(run)
    tags, _ = public_release_tags(private, url=str(public), names=["meterline-acme"])
    assert problem(private, "meterline-acme", tags) is None


def test_public_tags_are_fetched_outside_refs_tags(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    _repo(public)
    _, _, private = _private_copy(tmp_path)
    tags, _ = public_release_tags(private, url=str(public), names=["meterline-acme"])
    assert tags == {"meterline-acme-v0.1.0": "refs/public-tags/meterline-acme-v0.1.0"}
    assert _git(private, "tag", "--list").stdout.strip() == ""
    assert _git(private, "for-each-ref", "refs/public-tags").stdout.count("refs/public-tags/meterline-acme-v0.1.0") == 1


def test_an_unreachable_public_repository_is_reported_not_passed(tmp_path):
    _, _, private = _private_copy(tmp_path)
    tags, reason = public_release_tags(private, url=str(tmp_path / "no-such-repository"), names=["meterline-acme"])
    assert tags is None and "could not be reached" in reason


def test_only_tags_of_a_known_package_are_fetched(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    _, run = _repo(public)
    run("tag", "-a", "export/v9.9", "-m", "not a release tag")
    run("tag", "-a", "meterline-other-v1.0.0", "-m", "a package this repository does not have")
    _, _, private = _private_copy(tmp_path)
    tags, _ = public_release_tags(private, url=str(public), names=["meterline-acme"])
    assert list(tags) == ["meterline-acme-v0.1.0"]
