"""Tests for the "onto" mode of tools/export_public.py: a later public version lands as one new commit on top of the
public repository's history, and --scan-history --since scans only what a tag does not cover. Run: python3 -m pytest -q tools"""
import hashlib
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import export_public as ep  # noqa: E402
from package_list import in_working_repo  # noqa: E402

_REAL_INCLUDE = list(ep.INCLUDE)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME, EMAIL = "Pat Example", "pat@example.com"
# the term is assembled so this file passes the scanner it tests
_TERM = "plant" + "edterm"
_AWS_KEY = "AKIA" + "A" * 16

V1 = {"README.md": "# demo\nversion one\n", "packages/a/x.py": "x = 1\n", "packages/a/old.py": "old = 1\n",
      "docs/guide.md": "guide\n", "internal.txt": "kept private\n"}


@pytest.fixture(autouse=True)
def _small_include():
    ep.INCLUDE[:] = ["README.md", "packages", "docs"]
    yield
    ep.INCLUDE[:] = _REAL_INCLUDE


# ---- helpers ----

def _git(repo, *args, env=None):
    full = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", **(env or {})}
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=full).stdout.strip()


def _step(repo, message, files, remove=(), env=None):
    """One commit: write `files`, delete `remove`, commit everything."""
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
    for rel in remove:
        (repo / rel).unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message, env=env)


def _terms(tmp_path, *lines):
    d = tmp_path / "terms.txt"
    d.write_text("# c\n" + "\n".join(lines or ("zzzunused",)) + "\n")
    return d


def _private(tmp_path, v2_extra=None):
    """A private repository: export/v0.1 (lightweight tag), then export/v0.2 (annotated tag) that changes a file, removes a
    file, adds a file and adds a file outside the allowlist."""
    repo = tmp_path / "private"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _step(repo, "first", V1)
    _git(repo, "tag", "export/v0.1")
    _step(repo, "second", {"packages/a/x.py": "x = 2\n", "packages/a/new.py": "new = 1\n", "private-only/notes.txt": "n\n",
                           **(v2_extra or {})}, remove=["packages/a/old.py"])
    _git(repo, "tag", "-a", "export/v0.2", "-m", "export v0.2")
    return repo


def _first_version(tmp_path, repo, out, *extra, ref="export/v0.1", terms=()):
    return ep.main(["--repo", str(repo), "--ref", ref, "--out", str(out), "--denylist", str(_terms(tmp_path, *terms)),
                    "--author-name", NAME, "--author-email", EMAIL, "--no-gitleaks", *extra])


class World:
    def __init__(self, tmp_path, private, origin, clone):
        self.tmp_path, self.private, self.origin, self.clone = tmp_path, private, origin, clone

    def onto(self, *extra, tag="export/v0.2", terms=(), name=NAME, email=EMAIL, gitleaks=False):
        args = ["--repo", str(self.private), "--onto", str(self.clone), "--denylist", str(_terms(self.tmp_path, *terms)),
                "--author-name", name, "--author-email", email]
        if tag:
            args += ["--tag", tag]
        if not gitleaks:
            args.append("--no-gitleaks")
        return ep.main([*args, *extra])


def _world(tmp_path, v2_extra=None, trailer=True):
    """The private repository, the public origin (the first-version export of v0.1) and a clone of that origin."""
    private = _private(tmp_path, v2_extra)
    origin = tmp_path / "origin"
    assert _first_version(tmp_path, private, origin, *(["--tag", "export/v0.1"] if trailer else [])) == 0
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    return World(tmp_path, private, origin, clone)


def _snapshot(repo):
    """Everything an onto run must not touch when it refuses: refs, HEAD, index, work tree, object count."""
    def g(*a):
        return subprocess.run(["git", "--no-optional-locks", *a], cwd=repo, capture_output=True, text=True).stdout
    files = sorted((os.path.relpath(os.path.join(d, f), repo), os.path.getsize(os.path.join(d, f)))
                   for d, ds, fs in os.walk(repo) for f in fs if ".git" not in os.path.relpath(d, repo).split(os.sep))
    with open(os.path.join(repo, ".git", "index"), "rb") as fh:
        index = hashlib.sha256(fh.read()).hexdigest()
    return (g("rev-parse", "HEAD"), g("symbolic-ref", "-q", "HEAD"), g("for-each-ref"), g("status", "--porcelain"),
            g("count-objects", "-v"), index, files)


def _refusal(call):
    """The exit of a run that must refuse: its return value, or the sentence of its SystemExit. Never argparse's 2."""
    try:
        code = call()
    except SystemExit as stopped:
        code = stopped.code
    assert code not in (0, None), "the run did not refuse"
    assert code != 2, "argparse rejected the arguments instead of the tool refusing"
    return code


def _tree_of(repo, rev="HEAD"):
    return _git(repo, "rev-parse", rev + "^{tree}")


def _names(repo, rev="HEAD"):
    return _git(repo, "ls-tree", "-r", "--name-only", rev).splitlines()


# ---- 1: one commit on a new branch, the given identity, nothing pushed ----

def test_onto_makes_one_commit_on_a_new_branch_under_the_given_identity_and_pushes_nothing(tmp_path):
    w = _world(tmp_path)
    before = _git(w.clone, "rev-parse", "HEAD")
    remotes = _git(w.clone, "for-each-ref", "refs/remotes")
    origin_refs = _git(w.origin, "for-each-ref")
    config = _git(w.clone, "config", "--get-regexp", "^remote")
    assert w.onto() == 0
    assert _git(w.clone, "branch", "--show-current") == "export/v0.2"
    assert _git(w.clone, "rev-parse", "HEAD^") == before
    assert _git(w.clone, "rev-list", "--count", before + "..HEAD") == "1"
    assert _git(w.clone, "log", "-1", "--format=%an|%ae|%cn|%ce") == "|".join([NAME, EMAIL, NAME, EMAIL])
    assert _git(w.clone, "rev-parse", "main") == before  # the old branch is where it was
    assert _git(w.clone, "for-each-ref", "refs/remotes") == remotes
    assert _git(w.origin, "for-each-ref") == origin_refs
    assert _git(w.clone, "config", "--get-regexp", "^remote") == config
    assert _git(w.clone, "status", "--porcelain") == ""


# ---- 2: the new commit is the first-version export of the same ref ----

def test_the_onto_tree_equals_a_first_version_export_of_the_same_ref_and_the_work_tree_matches(tmp_path):
    w = _world(tmp_path)
    assert w.onto() == 0
    fresh = tmp_path / "fresh"
    assert _first_version(tmp_path, w.private, fresh, ref="export/v0.2") == 0
    assert _tree_of(w.clone) == _tree_of(fresh)
    names = _names(w.clone)
    assert "packages/a/old.py" not in names and "packages/a/new.py" in names
    assert (w.clone / "packages" / "a" / "x.py").read_text() == "x = 2\n"
    assert "private-only/notes.txt" not in names and "internal.txt" not in names
    on_disk = sorted(os.path.relpath(os.path.join(d, f), w.clone) for d, ds, fs in os.walk(w.clone)
                     for f in fs if ".git" not in os.path.relpath(d, w.clone).split(os.sep))
    assert on_disk == sorted(names)


# ---- 3: one trailer, no private commit id ----

def test_the_message_ends_with_one_export_tag_trailer_and_holds_no_private_commit_id(tmp_path):
    w = _world(tmp_path)
    assert w.onto() == 0
    message = _git(w.clone, "log", "-1", "--format=%B")
    assert message.splitlines()[0] == "meterline: public version v0.2"
    assert message.splitlines()[-1] == "Export-Tag: export/v0.2"
    assert message.count("Export-Tag:") == 1
    assert _git(w.clone, "log", "-1", "--format=%(trailers:key=Export-Tag,valueonly)") == "export/v0.2"
    ids = _git(w.private, "rev-list", "--all").splitlines()
    assert ids and all(i not in message and i[:7] not in message for i in ids)


# ---- 4: the scans stop the run before anything is written ----

@pytest.mark.parametrize("extra_files, kwargs, shown_kind", [
    ({"docs/extra.md": "built for " + _TERM + "\n"}, {}, "denylist term"),
    ({"packages/a/new.py": "key = '" + _AWS_KEY + "'\n"}, {}, "aws key"),
    ({}, {"message": "built for " + _TERM}, "denylist term"),
    ({}, {"name": "Pat " + _TERM}, "denylist term"),
    ({}, {"email": "pat@" + _TERM + ".example.com"}, "denylist term"),
], ids=["tree-term", "tree-secret", "message-term", "name-term", "email-term"])
def test_a_scan_hit_refuses_and_leaves_the_clone_alone(tmp_path, capsys, extra_files, kwargs, shown_kind):
    w = _world(tmp_path, v2_extra=extra_files)
    capsys.readouterr()
    before = _snapshot(w.clone)
    extra = ["--message", kwargs["message"]] if "message" in kwargs else []
    code = w.onto(*extra, terms=(_TERM,), **{k: v for k, v in kwargs.items() if k != "message"})
    shown = capsys.readouterr().out
    assert code == 1
    assert "HIT  " + shown_kind in shown and "FAILED: " in shown and "no commit made" in shown
    assert _TERM not in shown.lower() and _AWS_KEY not in shown
    assert _snapshot(w.clone) == before
    assert _git(w.clone, "branch", "--list", "export/v0.2") == ""


def test_a_failing_gitleaks_refuses_and_a_clean_one_lets_the_commit_through(tmp_path, monkeypatch, capsys):
    w = _world(tmp_path)
    _fake_gitleaks(tmp_path, monkeypatch, "8.30.1", scan_exit=1)
    before = _snapshot(w.clone)
    capsys.readouterr()
    assert w.onto(gitleaks=True) == 1
    assert "gitleaks found a leak" in capsys.readouterr().out
    assert _snapshot(w.clone) == before
    _fake_gitleaks(tmp_path, monkeypatch, "8.30.1", scan_exit=0)
    assert w.onto(gitleaks=True) == 0
    assert "gitleaks clean" in capsys.readouterr().out
    assert _git(w.clone, "branch", "--show-current") == "export/v0.2"


def _fake_gitleaks(tmp_path, monkeypatch, version, scan_exit=0):
    stub = tmp_path / "bin" / "gitleaks"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text("#!/bin/sh\nif [ \"$1\" = version ]; then echo %s; exit 0; fi\nexit %d\n" % (version, scan_exit))
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub.parent) + os.pathsep + os.environ["PATH"])


# ---- 5: refusals that change nothing ----

def _modify(w):
    (w.clone / "README.md").write_text("edited\n")


def _untracked(w):
    (w.clone / "stray.txt").write_text("stray\n")


def _detach(w):
    _git(w.clone, "switch", "-q", "--detach")


def _branch_exists(w):
    _git(w.clone, "branch", "export/v0.2")


def _remote_branch_exists(w):
    _git(w.clone, "update-ref", "refs/remotes/origin/export/v0.2", "HEAD")


def _parent_branch_exists(w):
    _git(w.clone, "branch", "export")  # a branch "export" blocks "export/v0.2"


@pytest.mark.parametrize("mutate, expected", [
    (_modify, "uncommitted"), (_untracked, "uncommitted"), (_detach, "branch"),
    (_branch_exists, "export/v0.2"), (_remote_branch_exists, "export/v0.2"), (_parent_branch_exists, "export"),
], ids=["modified-file", "untracked-file", "detached-head", "branch-exists", "remote-branch-exists", "parent-branch-exists"])
def test_a_clone_that_is_not_ready_refuses_and_nothing_changes(tmp_path, mutate, expected):
    w = _world(tmp_path)
    mutate(w)
    before = _snapshot(w.clone)
    code = _refusal(w.onto)
    assert expected in str(code)
    assert _snapshot(w.clone) == before


def test_onto_a_folder_that_is_not_a_git_repository_or_not_its_top_level_refuses(tmp_path):
    w = _world(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    w.clone, clone = plain, w.clone
    assert "git repository" in str(_refusal(w.onto))
    assert os.listdir(plain) == []
    w.clone = clone / "packages"
    before = _snapshot(clone)
    assert "top level" in str(_refusal(w.onto))
    assert _snapshot(clone) == before
    w.clone = tmp_path / "nowhere"
    assert "git repository" in str(_refusal(w.onto)) and not (tmp_path / "nowhere").exists()


def test_a_tag_that_is_not_in_the_private_repository_or_names_another_commit_refuses(tmp_path):
    w = _world(tmp_path)
    before = _snapshot(w.clone)
    assert "export/v9.9" in str(_refusal(lambda: w.onto(tag="export/v9.9")))
    assert "different commits" in str(_refusal(lambda: w.onto("--ref", "export/v0.1")))
    _git(w.private, "branch", "export/v0.3")  # a branch is no tag
    assert "export/v0.3" in str(_refusal(lambda: w.onto(tag="export/v0.3")))
    assert _snapshot(w.clone) == before


@pytest.mark.parametrize("extra, expected", [
    (["--out", "OUT"], "--out"), (["--scan-only"], "--scan-only"), (["--scan-history"], "--scan-history"),
    (["--allow-divergence", "--since", "export/v0.1"], "--since"),
], ids=["out", "scan-only", "scan-history", "since"])
def test_onto_refuses_the_options_it_cannot_be_combined_with(tmp_path, extra, expected):
    w = _world(tmp_path)
    extra = [str(tmp_path / "o") if x == "OUT" else x for x in extra]
    before = _snapshot(w.clone)
    assert expected in str(_refusal(lambda: w.onto(*extra)))
    assert not (tmp_path / "o").exists() and _snapshot(w.clone) == before


def test_onto_needs_a_tag_and_an_identity_and_allow_divergence_needs_onto(tmp_path):
    w = _world(tmp_path)
    before = _snapshot(w.clone)
    assert "--tag" in str(_refusal(lambda: w.onto(tag=None)))
    assert "--author-name" in str(_refusal(lambda: w.onto(name="")))
    assert "--author-email" in str(_refusal(lambda: w.onto(email="")))
    d = str(_terms(tmp_path))
    assert "--onto" in str(_refusal(lambda: ep.main(["--repo", str(w.private), "--ref", "export/v0.2", "--out", str(tmp_path / "x"),
                                                     "--denylist", d, "--scan-only", "--allow-divergence"])))
    assert _snapshot(w.clone) == before and not (tmp_path / "x").exists()


# ---- 6: divergence ----

def _outside_contribution(w):
    _git(w.clone, "-c", "user.name=c", "-c", "user.email=c@example.com", "switch", "-q", "main")
    (w.clone / "README.md").write_text("# demo\nversion one, fixed by a contributor\n")
    (w.clone / "docs" / "contrib.md").write_text("added by a contributor\n")
    _git(w.clone, "add", "README.md", "docs/contrib.md")
    _git(w.clone, "commit", "-q", "-m", "Merge a contribution")


def test_a_change_merged_on_the_public_repository_stops_the_export_unless_told_otherwise(tmp_path, capsys):
    w = _world(tmp_path)
    _outside_contribution(w)
    before = _snapshot(w.clone)
    capsys.readouterr()
    assert w.onto() == 1
    shown = capsys.readouterr().out
    assert "DIVERGED  README.md" in shown and "DIVERGED  docs/contrib.md" in shown
    assert "port" in shown and "no commit made" in shown
    assert "packages/a/x.py" not in shown
    assert _snapshot(w.clone) == before
    assert w.onto("--allow-divergence") == 0
    shown = capsys.readouterr().out
    assert "WARNING: --allow-divergence: the new commit overwrites these paths of the public repository:" in shown
    assert "README.md" in shown and "docs/contrib.md" in shown
    fresh = tmp_path / "fresh"
    assert _first_version(tmp_path, w.private, fresh, ref="export/v0.2") == 0
    assert _tree_of(w.clone) == _tree_of(fresh)
    assert _git(w.clone, "rev-parse", "HEAD^") == before[0].strip()


def test_a_clone_with_no_export_tag_trailer_asks_for_the_first_version_to_carry_one(tmp_path):
    w = _world(tmp_path, trailer=False)
    before = _snapshot(w.clone)
    code = _refusal(w.onto)
    assert "Export-Tag:" in str(code) and "--tag" in str(code)
    assert _snapshot(w.clone) == before


def test_the_previous_export_tag_is_not_needed_in_the_private_repository(tmp_path):
    w = _world(tmp_path)
    _git(w.private, "tag", "-d", "export/v0.1")
    assert w.onto() == 0
    assert _git(w.clone, "branch", "--show-current") == "export/v0.2"


_PORTED = {"README.md": "# demo\nversion one, fixed by a contributor\n", "docs/contrib.md": "added by a contributor\n"}


def _public_commit(w, files):
    for rel, body in files.items():
        (w.clone / rel).write_text(body)
    _git(w.clone, "add", *files)
    _git(w.clone, "commit", "-q", "-m", "Merge a contribution")


def _warned_paths(shown):
    lines = shown.splitlines()
    at = [i for i, line in enumerate(lines) if line.startswith("WARNING: --allow-divergence")]
    return [line.strip() for line in lines[at[0] + 1:] if line.startswith("  ")] if at else None


def test_a_public_change_that_was_ported_back_does_not_stop_the_export(tmp_path, capsys):
    w = _world(tmp_path, v2_extra=_PORTED)  # the private v0.2 holds the same change as the public commit
    _public_commit(w, _PORTED)
    capsys.readouterr()
    assert w.onto() == 0
    shown = capsys.readouterr().out
    assert "DIVERGED" not in shown and "WARNING" not in shown
    fresh = tmp_path / "fresh"
    assert _first_version(tmp_path, w.private, fresh, ref="export/v0.2") == 0
    assert _tree_of(w.clone) == _tree_of(fresh)


def test_a_public_change_ported_back_and_then_changed_again_privately_stops_the_export(tmp_path, capsys):
    w = _world(tmp_path, v2_extra={**_PORTED, "README.md": "# demo\nported, then changed again in private\n"})
    _public_commit(w, _PORTED)
    before = _snapshot(w.clone)
    capsys.readouterr()
    assert w.onto() == 1
    shown = capsys.readouterr().out
    assert [line for line in shown.splitlines() if line.startswith("DIVERGED")] == ["DIVERGED  README.md"]
    assert _snapshot(w.clone) == before
    assert w.onto("--allow-divergence") == 0
    assert _warned_paths(capsys.readouterr().out) == ["README.md"]
    assert (w.clone / "README.md").read_text() == "# demo\nported, then changed again in private\n"
    assert (w.clone / "docs" / "contrib.md").read_text() == "added by a contributor\n"  # ported and unchanged: kept


def test_a_change_of_the_export_rules_since_the_last_export_is_no_divergence(tmp_path, capsys, monkeypatch):
    w = _world(tmp_path)
    monkeypatch.setattr(ep, "EXCLUDE", [*ep.EXCLUDE, "docs/guide.md"])  # the v0.1 tree held this file; today's rules drop it
    capsys.readouterr()
    assert w.onto() == 0
    shown = capsys.readouterr().out
    assert "DIVERGED" not in shown and "WARNING" not in shown
    assert "docs/guide.md" not in _names(w.clone)


# ---- 7: nothing to export ----

def test_a_tag_on_the_same_public_tree_refuses_because_there_is_nothing_to_export(tmp_path):
    w = _world(tmp_path)
    _git(w.private, "switch", "-q", "-c", "side", "export/v0.1")
    _step(w.private, "private-only change", {"internal.txt": "changed, still not exported\n"})
    _git(w.private, "tag", "export/v0.1.1")
    before = _snapshot(w.clone)
    assert "nothing to export" in str(_refusal(lambda: w.onto(tag="export/v0.1.1")))
    assert _snapshot(w.clone) == before


# ---- 8: --scan-history --since ----

def _history(tmp_path, name, steps):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for message, files, *rest in steps:
        _step(repo, message, files, *rest)
    return repo


def _scan_history(tmp_path, repo, *extra):
    return ep.main(["--scan-history", "--repo", str(repo), "--denylist", str(_terms(tmp_path, _TERM)), *extra])


def _hits(shown):
    return {tuple(line.split("  ")[1:3]) for line in shown.splitlines() if line.startswith("HIT  ")}


def test_since_scans_only_the_commits_the_tag_does_not_cover_and_reports_what_the_full_scan_reports(tmp_path, capsys):
    repo = _history(tmp_path, "hist", [
        ("built for " + _TERM, {"a.txt": "one\n"}),
        ("second", {"b.txt": "has " + _TERM + " early\n"}),
        ("third", {"notes-" + _TERM + ".txt": "n\n"}),
        ("clean tree", {}, ["b.txt", "notes-" + _TERM + ".txt"]),
    ])
    _git(repo, "tag", "export/v0.1")
    _step(repo, "after " + _TERM, {"a.txt": "two\n"})
    main_message = _git(repo, "rev-parse", "HEAD")
    _step(repo, "late blob", {"c.txt": "late " + _TERM + " blob\n"}, env={"GIT_AUTHOR_NAME": "Pat " + _TERM})
    main_blob = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "-c", "side", "export/v0.1")
    _step(repo, "side " + _TERM, {"s.txt": "s\n"})
    side_message = _git(repo, "rev-parse", "HEAD")
    _step(repo, "side blob", {"d.txt": "side " + _TERM + " blob\n"})
    side_blob = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "main")

    assert _scan_history(tmp_path, repo) == 1
    full_shown = capsys.readouterr().out
    assert _scan_history(tmp_path, repo, "--since", "export/v0.1") == 1
    since_shown = capsys.readouterr().out
    covered = {i[:12] for i in _git(repo, "rev-list", "export/v0.1").splitlines()}
    scanned = {i[:12] for i in _git(repo, "rev-list", "--all", "^export/v0.1").splitlines()}
    full, since = _hits(full_shown), _hits(since_shown)
    assert {h[1] for h in full} & covered, "the term sits before the tag, so the full scan reports commits the tag covers"
    assert since and since == {h for h in full if h[1] in scanned}
    assert ("message", main_message[:12]) in since and ("message", side_message[:12]) in since
    assert ("content", main_blob[:12]) in since and ("content", side_blob[:12]) in since
    assert ("identity", main_blob[:12]) in since
    assert not {h[1] for h in since} & covered
    assert "%d commits on all refs of" % len(scanned) in since_shown and "not reachable from export/v0.1" in since_shown
    assert "scanned against 1 terms" in since_shown and _TERM not in since_shown.lower()
    assert "not reachable" not in full_shown  # without --since the line is the old one


def test_since_on_a_history_that_is_clean_after_the_tag_passes_although_older_commits_hold_the_term(tmp_path, capsys):
    repo = _history(tmp_path, "clean-after", [("built for " + _TERM, {"a.txt": "one\n"}), ("second", {"a.txt": "two\n"})])
    _git(repo, "tag", "export/v0.1")
    _step(repo, "third", {"a.txt": "three\n"})
    assert _scan_history(tmp_path, repo) == 1
    capsys.readouterr()
    assert _scan_history(tmp_path, repo, "--since", "export/v0.1") == 0
    shown = capsys.readouterr().out
    assert "history clean" in shown and "HIT" not in shown


def test_since_refuses_a_ref_that_does_not_resolve_and_needs_scan_history(tmp_path):
    repo = _history(tmp_path, "hist2", [("first", {"a.txt": "one\n"})])
    assert "export/none" in str(_refusal(lambda: _scan_history(tmp_path, repo, "--since", "export/none")))
    code = _refusal(lambda: ep.main(["--repo", str(repo), "--ref", "HEAD", "--out", str(tmp_path / "o"),
                                     "--denylist", str(_terms(tmp_path)), "--scan-only", "--since", "HEAD"]))
    assert "--scan-history" in str(code) and not (tmp_path / "o").exists()


# ---- 9: the first-version path ----

def test_first_version_adds_the_trailer_only_when_a_tag_is_given(tmp_path):
    private = _private(tmp_path)
    plain = tmp_path / "plain"
    assert _first_version(tmp_path, private, plain) == 0
    assert _git(plain, "log", "-1", "--format=%B") == "meterline: first public version"
    tagged = tmp_path / "tagged"
    assert _first_version(tmp_path, private, tagged, "--tag", "export/v0.1") == 0
    message = _git(tagged, "log", "-1", "--format=%B")
    assert message == "meterline: first public version\n\nExport-Tag: export/v0.1"
    assert _tree_of(tagged) == _tree_of(plain)
    # a tag alone names the ref; a message that ends in blank lines still ends in exactly one trailer
    by_tag = tmp_path / "by-tag"
    assert ep.main(["--repo", str(private), "--tag", "export/v0.2", "--out", str(by_tag), "--denylist", str(_terms(tmp_path)),
                    "--author-name", NAME, "--author-email", EMAIL, "--no-gitleaks", "--message", "custom text \n\n"]) == 0
    assert _git(by_tag, "log", "-1", "--format=%B") == "custom text\n\nExport-Tag: export/v0.2"
    assert "packages/a/new.py" in _names(by_tag)


def test_first_version_refuses_a_tag_that_does_not_match_the_ref_or_a_message_with_its_own_trailer(tmp_path):
    private = _private(tmp_path)
    out = tmp_path / "out"
    assert "different commits" in str(_refusal(lambda: _first_version(tmp_path, private, out, "--tag", "export/v0.2")))
    assert not out.exists()
    assert "Export-Tag" in str(_refusal(lambda: _first_version(tmp_path, private, out, "--tag", "export/v0.1",
                                                              "--message", "x\n\nExport-Tag: export/v0.0")))
    assert not out.exists()


# ---- 10: docs/dev/RELEASING.md ----

def _releasing():
    path = os.path.join(ROOT, "docs", "dev", "RELEASING.md")
    if not os.path.exists(path) and not in_working_repo():
        pytest.skip("the maintainer's release guide is not part of the public repository")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _later_versions():
    text = _releasing()
    assert "\n## Later public versions\n" in text
    return text.split("\n## Later public versions\n", 1)[1].split("\n## ", 1)[0]


def test_releasing_guide_has_a_short_section_on_later_public_versions():
    section = _later_versions()
    flat = " ".join(section.split())
    at = 0
    for key in ("export/v", "--scan-history --since", "--onto", "pull request", "Each release"):
        found = flat.find(key, at)
        assert found >= 0, key + " is missing, or out of order"
        at = found
    assert "starts with `meterline`" in flat and "release workflow" in flat
    assert "git format-patch" in flat and "git am" in flat
    assert "--allow-divergence" in flat and "port" in flat
    assert len(("## Later public versions\n" + section).rstrip().splitlines()) <= 40


def test_releasing_guide_sits_between_scan_limits_and_publishing_and_step_3_exports_with_the_tag():
    text = _releasing()
    order = [text.find("\n## " + h + "\n") for h in ("Scan limits", "Later public versions", "Publishing to PyPI")]
    assert all(i >= 0 for i in order) and order == sorted(order)
    step_3 = text.split("\n3. Check the tools", 1)[1].split("\n4. Create the empty public repository", 1)[0]
    assert "--tag export/v0.1" in step_3 and "Export-Tag:" in step_3


# ---- v0.3 hardening C-3: a contributor's commit cannot move the baseline -------------------------------------------------

OTHER = "someone@example.com"


def _merged_contributor_commit(w, trailer="Export-Tag: export/v0.9"):
    """On the public clone: a contributor branch with a commit that carries an Export-Tag line, changes docs/guide.md and is
    merged into the branch the clone is on (a merge commit by the maintainer). Returns the maintainer's export commit."""
    export_commit = _git(w.clone, "rev-parse", "HEAD")
    branch = _git(w.clone, "branch", "--show-current")
    _git(w.clone, "checkout", "-q", "-b", "contributor")
    _step(w.clone, "A contribution\n\n" + trailer, {"docs/guide.md": "guide, improved by a contributor\n"},
          env={"GIT_AUTHOR_EMAIL": OTHER, "GIT_COMMITTER_EMAIL": OTHER})
    _git(w.clone, "checkout", "-q", branch)
    _git(w.clone, "merge", "-q", "--no-ff", "-m", "Merge pull request 1 from contributor", "contributor")
    return export_commit


def test_the_baseline_stays_the_maintainers_export_commit_when_a_merged_commit_carries_the_trailer(tmp_path):
    w = _world(tmp_path)
    export_commit = _merged_contributor_commit(w)
    assert ep.last_export_commit(str(w.clone), EMAIL) == export_commit


def test_the_ignored_commit_is_named_in_one_notice_line(tmp_path, capsys):
    w = _world(tmp_path)
    _merged_contributor_commit(w)
    contributor = _git(w.clone, "rev-parse", "contributor")
    ep.last_export_commit(str(w.clone), EMAIL)
    notices = [line for line in capsys.readouterr().out.splitlines() if line.startswith("notice:")]
    assert len(notices) == 1 and contributor[:12] in notices[0] and "first-parent" in notices[0]


def test_a_first_parent_commit_by_another_author_with_the_trailer_is_ignored_too(tmp_path, capsys):
    w = _world(tmp_path)
    export_commit = _git(w.clone, "rev-parse", "HEAD")
    _step(w.clone, "Looks like an export\n\nExport-Tag: export/v0.9", {"docs/guide.md": "changed\n"},
          env={"GIT_AUTHOR_EMAIL": OTHER, "GIT_COMMITTER_EMAIL": OTHER})
    assert ep.last_export_commit(str(w.clone), EMAIL) == export_commit
    assert "its author is not the maintainer" in capsys.readouterr().out


def test_a_change_merged_on_the_public_side_still_stops_the_next_export_as_diverged(tmp_path, capsys):
    """With the baseline on the contributor's commit the change looked like part of the last export, and the new commit
    silently undid it."""
    w = _world(tmp_path)
    _merged_contributor_commit(w)
    code = w.onto()
    out = capsys.readouterr().out
    assert code == 1 and "DIVERGED  docs/guide.md" in out
