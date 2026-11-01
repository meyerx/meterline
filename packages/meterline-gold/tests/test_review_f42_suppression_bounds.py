"""F-42: every boundary of the small-group rule is pinned by a named test, and keys stay out of git.

The primary rule hides a cell of fewer than ``k`` persons; the receiver clause hides a cell in which, once the persons
who know their own figures are taken out, fewer than ``k`` remain. Each is pinned at k - 1, k and k + 1, for k = 5 (the
shipped examples) and k = 2 (the smallest the policy allows).
"""

import re
import subprocess
from pathlib import Path

import duckdb
import pytest

from gold_helpers import REPO

MONTH = "2026-05"


def _suppressed(k, cells):
    """{(month, cell_id): reason} that run.run_privacy returns for a list of (cell_id, parent, persons, receivers)."""
    from meterline_gold import run

    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE cells (month VARCHAR, cell_id VARCHAR, parent_cell_id VARCHAR, persons_list VARCHAR[], "
        "receivers_list VARCHAR[])")
    for cell_id, parent, persons, receivers in cells:
        con.execute("INSERT INTO cells VALUES (?, ?, ?, ?, ?)", [MONTH, cell_id, parent, sorted(persons), sorted(receivers)])
    return run.run_privacy(con, k)


def _org(k, size_a, receivers_a):
    """An organisation of three units: A of ``size_a`` persons (the first ``receivers_a`` of them know their own
    figures), and B and C of k + 3 persons each, whose head is their only receiver."""
    a = ["a%02d" % i for i in range(size_a)]
    b = ["b%02d" % i for i in range(k + 3)]
    c = ["c%02d" % i for i in range(k + 3)]
    return [
        ("org:", "", a + b + c, []),
        ("unit:A", "org:", a, a[:receivers_a]),
        ("unit:B", "org:", b, b[:1]),
        ("unit:C", "org:", c, c[:1]),
    ]


@pytest.mark.parametrize("k", [2, 5])
@pytest.mark.parametrize("offset,suppressed", [(-1, True), (0, False), (1, False)])
def test_privacy_suppression_exact_k_primary_rule(k, offset, suppressed):
    """A cell of k - 1 persons is hidden (reason k); one of exactly k, or k + 1, is published and hides nothing else."""
    result = _suppressed(k, _org(k, k + offset, 0))
    if suppressed:
        assert result[(MONTH, "unit:A")] == "k"
        assert set(result.values()) <= {"k", "secondary"}
    else:
        assert result == {}


@pytest.mark.parametrize("k", [2, 5])
@pytest.mark.parametrize("offset,suppressed", [(-1, True), (0, False), (1, False)])
def test_privacy_suppression_exact_k_receiver_clause(k, offset, suppressed):
    """With one receiver in the cell, k persons leave k - 1 who do not know their own figure: hidden (reason k).
    k + 1 persons leave exactly k: published."""
    size = k + 1 + offset  # k, k + 1, k + 2 persons; one of them a receiver
    result = _suppressed(k, _org(k, size, 1))
    if suppressed:
        assert result[(MONTH, "unit:A")] == "k"
    else:
        assert result == {}


@pytest.mark.parametrize("k", [2, 5])
def test_privacy_suppression_exact_k_one_person_cell_is_hidden_for_every_k(k):
    assert _suppressed(k, _org(k, 1, 0))[(MONTH, "unit:A")] == "k"


@pytest.mark.parametrize("k", [2, 5])
def test_privacy_suppression_exact_k_two_receivers_move_the_boundary_by_two(k):
    """The receiver boundary follows the number of receivers: k + 1 persons with two receivers leave k - 1."""
    result = _suppressed(k, _org(k, k + 1, 2))
    assert result[(MONTH, "unit:A")] == "k"
    assert _suppressed(k, _org(k, k + 2, 2)) == {}


def test_privacy_suppression_exact_k_the_boundaries_are_not_all_shared_with_the_negative_control():
    """Negative control: the same org with every cell large enough hides nothing, so the cases above fail for
    the reason they name and not because the helper always hides something."""
    assert _suppressed(5, _org(5, 9, 0)) == {}


# ---- keys stay out of git -----------------------------------------------------------------------------------

_KEY_PATTERNS = [
    re.compile(r"METERLINE_GOLD_KEY\s*[=:]\s*[\"']?([A-Za-z0-9+/_=.\-]{32,})"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"),
]
# values that say they are placeholders or the published test key
_PLACEHOLDERS = ("not-a-secret", "keep-out-of-git", "a-key-of-at-least", "your-key", "example")


def key_findings(root):
    """(path, what) for every tracked file of the git work tree at ``root`` that holds a key-shaped value, and for
    every tracked file whose name is a .env file. Returns None when ``root`` is not a git work tree."""
    run = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True,
                         env={"GIT_OPTIONAL_LOCKS": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"})
    if run.returncode != 0:
        return None
    found = []
    for name in run.stdout.decode("utf-8").split("\0"):
        if not name:
            continue
        path = Path(root) / name
        if Path(name).name == ".env" or Path(name).name.startswith(".env."):
            found.append((name, "an env file is tracked"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in _KEY_PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0)
                if not any(word in value for word in _PLACEHOLDERS):
                    found.append((name, "a key-shaped value"))
    return found


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), check=True, capture_output=True,
                   env={"GIT_OPTIONAL_LOCKS": "0", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd),
                        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})


def test_keys_stay_out_of_git_no_tracked_file_holds_a_key():
    findings = key_findings(REPO)
    if findings is None:
        pytest.skip("not a git work tree")
    assert findings == []


def test_keys_stay_out_of_git_env_files_are_ignored():
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignore and ".env.*" in ignore


def test_keys_stay_out_of_git_negative_control(tmp_path):
    """The scanner flags a tracked key, a tracked .env file and a tracked token, and passes the placeholders."""
    _git(["init", "-q"], tmp_path)
    (tmp_path / "notes.md").write_text("METERLINE_GOLD_KEY=" + "q7Zk" * 12 + "\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SOMETHING=1\n", encoding="utf-8")
    (tmp_path / "token.txt").write_text("sk-ant-" + "a1B2" * 10 + "\n", encoding="utf-8")
    (tmp_path / "ok.md").write_text('export METERLINE_GOLD_KEY="a-key-of-at-least-32-bytes-that-you-keep-out-of-git"\n',
                                    encoding="utf-8")
    _git(["add", "notes.md", ".env", "token.txt", "ok.md"], tmp_path)
    found = key_findings(tmp_path)
    assert sorted(name for name, _ in found) == [".env", "notes.md", "token.txt"]
