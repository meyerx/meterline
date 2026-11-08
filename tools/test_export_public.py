"""Tests for tools/export_public.py. Run: python3 -m pytest -q tools"""
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import export_public as ep  # noqa: E402

_REAL_INCLUDE = list(ep.INCLUDE)
# private markers are assembled here so this file itself passes the scanner it tests
_RULES = "CLAUDE" + ".md"
_BACKLOG = "BACKLOG" + "-std.md"
_TICKET = "MET" + "R-12"
_USERS = "/Us" + "ers/someone/x"
_HOME = "/ho" + "me/someone/x"


@pytest.fixture(autouse=True)
def _restore_include():
    yield
    ep.INCLUDE[:] = _REAL_INCLUDE


def _repo(tmp_path, files, keep_real_include=False):
    r = tmp_path / "src"
    r.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    for rel, body in files.items():
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    subprocess.run(["git", "add", "--", *files], cwd=r, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=r, check=True, env=env)
    if keep_real_include:
        return r
    # a small fake repo holds only some of the allowlisted paths; the tool refuses a missing one
    ep.INCLUDE[:] = [p for p in _REAL_INCLUDE if any(f.split("/")[0] == p for f in files)]
    return r


def _run(tmp_path, repo, terms, *extra):
    d = tmp_path / "terms.txt"
    d.write_text("\n".join(terms) + "\n")
    out = tmp_path / "out"
    code = ep.main(["--repo", str(repo), "--ref", "HEAD", "--out", str(out), "--denylist", str(d),
                    "--author-name", "t", "--author-email", "t@example.com", "--no-gitleaks", *extra])
    return code, out


def test_clean_export_commits_once_and_leaves_private_files_out(tmp_path):
    repo = _repo(tmp_path, {"README.md": "hi\n", "packages/a/x.py": "x = 1\n",
                            _RULES: "private\n", _BACKLOG: "b\n", "scripts/t.py": "t\n"})
    code, out = _run(tmp_path, repo, ["secretterm"])
    assert code == 0
    assert sorted(os.listdir(out)) == [".git", "README.md", "packages"]
    log = subprocess.run(["git", "log", "--oneline"], cwd=out, capture_output=True, text=True).stdout
    assert len(log.splitlines()) == 1


def test_denylist_hit_stops_before_commit_and_hides_the_term(tmp_path, capsys):
    repo = _repo(tmp_path, {"README.md": "mentions Acme Corp here\n"})
    code, out = _run(tmp_path, repo, ["# c", "acme corp"])
    assert code == 1
    shown = capsys.readouterr().out
    assert "denylist term" in shown and "line 2" in shown and "acme" not in shown.lower()
    assert not (out / ".git").exists()


def test_secret_email_and_home_path_are_caught(tmp_path):
    repo = _repo(tmp_path, {"README.md": "key AKIA" + "A" * 16 + "\nmail a@" + "corp.test.org" + "\nsee " + _USERS + "\n"})
    hits = ep.scan(str(_export(tmp_path, repo)), [(1, "zzz")])
    kinds = {h[0] for h in hits}
    assert {"aws key", "email outside example domains", "absolute home path"} <= kinds


def _export(tmp_path, repo):
    out = tmp_path / "o2"
    out.mkdir()
    ep.export_tree(str(repo), "HEAD", str(out))
    return out


def test_private_working_traces_are_caught(tmp_path):
    body = "see " + _TICKET + " and MET" + "R33\nfiles " + _BACKLOG + " and " + _RULES + "\npar" + "ked by the owner\n"
    repo = _repo(tmp_path, {"README.md": body})
    kinds = {h[0] for h in ep.scan(str(_export(tmp_path, repo)), [(1, "zzz")])}
    assert {"backlog ticket id", "private backlog file", "private rules file", "parked-by note"} <= kinds


def test_ordinary_words_are_not_mistaken_for_traces(tmp_path):
    repo = _repo(tmp_path, {"README.md": "metrics, a METRIC5 tool, meterline, parked cars\n"})
    assert ep.scan(str(_export(tmp_path, repo)), [(1, "zzz")]) == []


def test_a_ticket_id_in_a_file_name_is_caught(tmp_path):
    repo = _repo(tmp_path, {"packages/p/tests/test_done_check_MET" + "R33_x.py": "x = 1\n"})
    kinds = {h[0] for h in ep.scan(str(_export(tmp_path, repo)), [(1, "zzz")])}
    assert "backlog ticket id" in kinds


# ---- F-09 / F-10: the control is the squashed export; it refuses rather than skips ----

def test_private_trace_patterns_refused(tmp_path, capsys):
    body = "ticket " + _TICKET + "\nfile " + _BACKLOG + "\nrules " + _RULES + "\npath " + _HOME + "\n"
    repo = _repo(tmp_path, {"README.md": body})
    code, out = _run(tmp_path, repo, ["zzz"])
    shown = capsys.readouterr().out
    assert code == 1 and not (out / ".git").exists()
    for kind in ("backlog ticket id", "private backlog file", "private rules file", "absolute home path"):
        assert kind in shown


def test_missing_include_path_refuses(tmp_path):
    repo = _repo(tmp_path, {"README.md": "hi\n"}, keep_real_include=True)
    with pytest.raises(SystemExit) as e:
        _run(tmp_path, repo, ["zzz"])
    assert e.value.code not in (0, None)
    assert "missing" in str(e.value.code)
    assert not (tmp_path / "out" / ".git").exists()


def test_author_required_without_scan_only(tmp_path):
    repo = _repo(tmp_path, {"README.md": "hi\n"})
    d = tmp_path / "terms.txt"
    d.write_text("zzz\n")
    with pytest.raises(SystemExit) as e:
        ep.main(["--repo", str(repo), "--ref", "HEAD", "--out", str(tmp_path / "out"),
                 "--denylist", str(d), "--no-gitleaks"])
    assert "--author-name is required" in str(e.value.code)
    assert not (tmp_path / "out" / ".git").exists()
    # scan-only needs no identity
    assert ep.main(["--repo", str(repo), "--ref", "HEAD", "--out", str(tmp_path / "out2"),
                    "--denylist", str(d), "--scan-only"]) == 0


def test_author_identity_scanned(tmp_path, capsys):
    repo = _repo(tmp_path, {"README.md": "hi\n"})
    d = tmp_path / "terms.txt"
    d.write_text("# c\nzzz\nacmecorp\n")
    for name, email in (("Pat Acmecorp", "pat@example.com"), ("Pat", "pat@acme" + "corp.test")):
        out = tmp_path / ("out-" + name.replace(" ", ""))
        code = ep.main(["--repo", str(repo), "--ref", "HEAD", "--out", str(out), "--denylist", str(d),
                        "--author-name", name, "--author-email", email, "--no-gitleaks"])
        shown = capsys.readouterr().out
        assert code == 1 and not (out / ".git").exists()
        assert "denylist term" in shown and "line 3" in shown and "acmecorp" not in shown.lower()


def test_commit_message_is_scanned(tmp_path, capsys):
    repo = _repo(tmp_path, {"README.md": "hi\n"})
    code, out = _run(tmp_path, repo, ["zzz", "acmecorp"], "--message", "built at AcmeCorp")
    assert code == 1 and not (out / ".git").exists()
    assert "acmecorp" not in capsys.readouterr().out.lower()


def test_no_denylist_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv("METERLINE_DENYLIST", raising=False)
    repo = _repo(tmp_path, {"README.md": "hi\n"})
    with pytest.raises(SystemExit) as e:
        ep.main(["--repo", str(repo), "--ref", "HEAD", "--out", str(tmp_path / "out"),
                 "--author-name", "t", "--author-email", "t@example.com"])
    assert "denylist" in str(e.value.code)
    assert not (tmp_path / "out").exists() or not os.listdir(tmp_path / "out")


def test_gitleaks_absent_refuses_to_commit_unless_told_otherwise(tmp_path, monkeypatch):
    monkeypatch.setattr(ep.shutil, "which", lambda _name: None)
    repo = _repo(tmp_path, {"README.md": "hi\n"})
    d = tmp_path / "terms.txt"
    d.write_text("zzz\n")
    base = ["--repo", str(repo), "--ref", "HEAD", "--denylist", str(d),
            "--author-name", "t", "--author-email", "t@example.com"]
    with pytest.raises(SystemExit) as e:
        ep.main([*base, "--out", str(tmp_path / "a")])
    assert "gitleaks" in str(e.value.code)
    assert not (tmp_path / "a" / ".git").exists()
    assert ep.main([*base, "--out", str(tmp_path / "b"), "--no-gitleaks"]) == 0


def test_gitleaks_finding_refuses_to_commit(tmp_path, monkeypatch):
    stub = tmp_path / "bin" / "gitleaks"
    stub.parent.mkdir()
    stub.write_text("#!/bin/sh\nif [ \"$1\" = version ]; then echo 8.30.1; exit 0; fi\nexit 1\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub.parent) + os.pathsep + os.environ["PATH"])
    repo = _repo(tmp_path, {"README.md": "hi\n"})
    d = tmp_path / "terms.txt"
    d.write_text("zzz\n")
    code = ep.main(["--repo", str(repo), "--ref", "HEAD", "--out", str(tmp_path / "out"), "--denylist", str(d),
                    "--author-name", "t", "--author-email", "t@example.com"])
    assert code == 1 and not (tmp_path / "out" / ".git").exists()


# ---- B2: every new top-level path is allowlisted; the build contract copy stays private ----

def test_real_include_names_the_agent_files_and_the_examples():
    assert {"AGENTS.md", "llms.txt", "examples"} <= set(_REAL_INCLUDE)


def test_examples_are_exported(tmp_path):
    repo = _repo(tmp_path, {"README.md": "hi\n", "examples/01/README.md": "e\n"})
    code, out = _run(tmp_path, repo, ["zzz"])
    assert code == 0 and (out / "examples" / "01" / "README.md").is_file()


def test_agent_files_are_exported(tmp_path):
    repo = _repo(tmp_path, {"README.md": "hi\n", "AGENTS.md": "# a\n", "llms.txt": "# l\n", "notes.txt": "n\n"})
    code, out = _run(tmp_path, repo, ["zzz"])
    assert code == 0
    assert sorted(os.listdir(out)) == [".git", "AGENTS.md", "README.md", "llms.txt"]


@pytest.mark.parametrize("contract", ["docs/dev/meterline-integration-base-SPEC-v1.0-2026-10-29.md",
                                      "docs/dev/meterline-improvements-1-SPEC-v1.0-2026-10-29.md",
                                      "docs/dev/meterline-integrate-pr2a-SPEC-v1.0-2026-11-03.md",
                                      "docs/dev/meterline-integrate-pr2b-SPEC-v1.0-2026-11-03.md",
                                      "docs/dev/meterline-v03-hardening-SPEC-v1.0-2026-11-05.md"])
def test_the_build_contract_copy_is_left_out_of_the_export(tmp_path, contract):
    assert contract in ep.EXCLUDE
    repo = _repo(tmp_path, {"README.md": "[d](docs/dev/README.md)\n", "docs/dev/README.md": "d\n", contract: "contract\n"})
    code, out = _run(tmp_path, repo, ["zzz"])
    assert code == 0
    assert sorted(os.listdir(out / "docs" / "dev")) == ["README.md"]


def test_no_exported_file_links_the_integrate_pr2a_spec():
    """R-9 of the integrate part 1 spec: the spec copy is left out of the export, so nothing the export keeps may link it.
    The files that may name it are the ones the export leaves out or prunes (docs/dev is only kept where a public document
    links into it) and the tool and test files that list it."""
    spec = "meterline-integrate-pr2a-SPEC-v1.0-2026-11-03.md"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    may_name_it = {"docs/dev/README.md", "docs/dev/plans/integrate-pr2a-progress.md", "tools/export_public.py",
                   "tools/test_export_public.py", "docs/dev/" + spec}
    listed = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    kept = [f for f in listed if f.split("/")[0] in ep.INCLUDE and f not in ep.EXCLUDE and f.endswith(ep.LINK_SUFFIXES)
            and f not in may_name_it and os.path.isfile(os.path.join(root, f))]
    assert kept
    naming = [f for f in kept if spec in open(os.path.join(root, f), encoding="utf-8", errors="replace").read()]
    assert naming == []


def test_no_exported_file_links_the_integrate_pr2b_spec():
    """R-5 of the integrate part 2 spec, as R-9 of part 1: the spec copy is left out of the export, so nothing the export
    keeps may link it. Only the files the export leaves out or prunes, and the tool and test files that list it, may name it."""
    spec = "meterline-integrate-pr2b-SPEC-v1.0-2026-11-03.md"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    may_name_it = {"docs/dev/README.md", "docs/dev/plans/integrate-pr2b-progress.md", "tools/export_public.py",
                   "tools/test_export_public.py", "docs/dev/" + spec}
    listed = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    kept = [f for f in listed if f.split("/")[0] in ep.INCLUDE and f not in ep.EXCLUDE and f.endswith(ep.LINK_SUFFIXES)
            and f not in may_name_it and os.path.isfile(os.path.join(root, f))]
    assert kept
    naming = [f for f in kept if spec in open(os.path.join(root, f), encoding="utf-8", errors="replace").read()]
    assert naming == []


def test_no_exported_file_links_the_v03_hardening_spec():
    """The spec copy of the v0.3 hardening is left out of the export, so nothing the export keeps may link it, as for the
    integrate specs. Only the files the export leaves out or prunes, and the tool and test files that list it, may name it."""
    spec = "meterline-v03-hardening-SPEC-v1.0-2026-11-05.md"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    may_name_it = {"docs/dev/README.md", "docs/dev/plans/v03-hardening-plan.md", "docs/dev/plans/v03-hardening-progress.md",
                   "tools/export_public.py", "tools/test_export_public.py", "docs/dev/" + spec}
    listed = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    kept = [f for f in listed if f.split("/")[0] in ep.INCLUDE and f not in ep.EXCLUDE and f.endswith(ep.LINK_SUFFIXES)
            and f not in may_name_it and os.path.isfile(os.path.join(root, f))]
    assert kept
    naming = [f for f in kept if spec in open(os.path.join(root, f), encoding="utf-8", errors="replace").read()]
    assert naming == []


# ---- MF-5: docs/dev leaves the export unless a public document links it; process traces are refused ----

_SENS = "sensit" + "ivity: internal"      # assembled, so this file does not match its own pattern
_ACCEPTED = "accepted" + "_when: when the owner renames this file"
_SPEC_FM = "---\ntype: spec\nstatus: accepted\n" + _SENS + "\n" + _ACCEPTED + "\n---\n\n# A spec\n\nbody text\n"
_ADMIN_SPEC = "docs/dev/meterline-anthropic-admin-SPEC-v1.0-2026-10-17.md"


def _dev_files(out):
    return sorted(str(p.relative_to(out)) for p in (out / "docs" / "dev").rglob("*") if p.is_file()) \
        if (out / "docs" / "dev").exists() else []


def test_a_docs_dev_file_that_no_public_document_links_is_left_out(tmp_path):
    repo = _repo(tmp_path, {"README.md": "see [the spec](docs/dev/kept-SPEC-v1.0.md)\n", "docs/dev/kept-SPEC-v1.0.md": "kept\n",
                            "docs/dev/other-SPEC-v1.0.md": "x\n", "docs/dev/plans/build-log.md": "log\n"})
    code, out = _run(tmp_path, repo, ["zzz"])
    assert code == 0
    assert _dev_files(out) == ["docs/dev/kept-SPEC-v1.0.md"]


def test_a_path_mentioned_in_a_public_document_keeps_the_file(tmp_path):
    # a decision record cites a spec by path in backticks, and a package README by a github.com URL
    repo = _repo(tmp_path, {"docs/decisions/0001.md": "Source: `docs/dev/a.md` section 2.\n",
                            "packages/p/README.md": "[b](https://github.com/o/r/blob/main/docs/dev/history/b.md)\n",
                            "docs/dev/a.md": "a\n", "docs/dev/history/b.md": "b\n", "docs/dev/c.md": "c\n"})
    code, out = _run(tmp_path, repo, ["zzz"])
    assert code == 0 and _dev_files(out) == ["docs/dev/a.md", "docs/dev/history/b.md"]


def test_a_relative_link_from_a_public_doc_keeps_the_file(tmp_path):
    repo = _repo(tmp_path, {"docs/how-to/x.md": "[spec](../dev/s.md)\n", "docs/dev/s.md": "s\n", "docs/dev/t.md": "t\n"})
    code, out = _run(tmp_path, repo, ["zzz"])
    assert code == 0 and _dev_files(out) == ["docs/dev/s.md"]


def test_a_link_to_a_left_out_docs_dev_file_fails_the_export(tmp_path, capsys, monkeypatch):
    repo = _repo(tmp_path, {"docs/records.md": "spec: `" + _ADMIN_SPEC + "`\n", _ADMIN_SPEC: "spec\n"})
    monkeypatch.setattr(ep, "EXCLUDE", [*ep.EXCLUDE, _ADMIN_SPEC])
    code, out = _run(tmp_path, repo, ["zzz"])
    shown = capsys.readouterr().out
    assert code == 1 and not (out / ".git").exists()
    assert "link to a docs/dev file left out of the export" in shown and "docs/records.md" in shown


def test_a_kept_docs_dev_file_linking_a_left_out_one_fails_the_export(tmp_path, capsys):
    repo = _repo(tmp_path, {"README.md": "[a](docs/dev/a.md)\n", "docs/dev/a.md": "see [b](b.md)\n", "docs/dev/b.md": "b\n"})
    code, out = _run(tmp_path, repo, ["zzz"])
    shown = capsys.readouterr().out
    assert code == 1 and "link to a docs/dev file left out of the export" in shown and "docs/dev/a.md" in shown


def test_scan_flags_a_docs_dev_file_that_no_public_document_links(tmp_path):
    root = tmp_path / "tree"
    (root / "docs" / "dev").mkdir(parents=True)
    (root / "README.md").write_text("hi\n")
    (root / "docs" / "dev" / "orphan.md").write_text("o\n")
    kinds = {h[0] for h in ep.scan(str(root), [(1, "zzz")])}
    assert "docs/dev file no public document links" in kinds


def test_process_front_matter_keys_are_dropped_from_a_kept_file_and_the_rest_stays(tmp_path):
    repo = _repo(tmp_path, {"README.md": "[s](docs/dev/s.md)\n", "docs/dev/s.md": _SPEC_FM})
    code, out = _run(tmp_path, repo, ["zzz"])
    text = (out / "docs" / "dev" / "s.md").read_text()
    assert code == 0
    assert _SENS.split(":")[0] not in text and "accepted" + "_when" not in text
    assert text.startswith("---\ntype: spec\nstatus: accepted\n---\n") and text.endswith("# A spec\n\nbody text\n")


def test_front_matter_keys_are_dropped_from_every_exported_markdown_file(tmp_path):
    repo = _repo(tmp_path, {"docs/api-shapes/x/README.md": "---\ntype: reference\n" + _SENS + "\n---\n\n# X\n"})
    code, out = _run(tmp_path, repo, ["zzz"])
    assert code == 0 and (out / "docs" / "api-shapes" / "x" / "README.md").read_text() == "---\ntype: reference\n---\n\n# X\n"


def test_the_process_key_pattern_catches_what_the_export_did_not_strip(tmp_path):
    # a .txt file is not markdown, so the strip leaves it and the scan must refuse it
    repo = _repo(tmp_path, {"llms.txt": "# l\n" + _SENS + "\n" + _ACCEPTED + "\n"})
    kinds = {h[0] for h in ep.scan(str(_export(tmp_path, repo)), [(1, "zzz")])}
    assert "process front-matter key" in kinds


# ---- X1: the owner's private id shapes come from the term file (`re:` lines), not from this tool's code ----

# a generic id shape stands in for a private one: nothing here spells a real prefix
_ID = "ZZ" + "Z-123"
_ID_RE = "re:\\b" + "ZZ" + "Z-[0-9]+\\b"


def _terms(tmp_path, *lines):
    d = tmp_path / "terms.txt"
    d.write_text("\n".join(lines) + "\n")
    return d


def test_a_re_line_of_the_term_file_catches_a_planted_id_by_line_number(tmp_path):
    repo = _repo(tmp_path, {"README.md": "row " + _ID + " is parked\n", "packages/p/ok.py": "x = 1\n"})
    terms = ep.load_terms(_terms(tmp_path, "# comment", "plainterm", _ID_RE))
    hits = ep.scan(str(_export(tmp_path, repo)), terms)
    assert ("denylist term", "README.md", "line 3 of the denylist file") in hits
    assert [h for h in hits if h[1] != "README.md"] == []


def test_a_re_line_is_case_insensitive_and_does_not_hit_other_shapes(tmp_path):
    repo = _repo(tmp_path, {"README.md": "low " + _ID.lower() + "\n", "docs/other.md": "ZZZ12 ZZZ-x A" + _ID + "b-1 ZZ-12\n"})
    terms = ep.load_terms(_terms(tmp_path, _ID_RE))
    hits = ep.scan(str(_export(tmp_path, repo)), terms)
    assert [h[1] for h in hits] == ["README.md"]


def test_a_re_line_hit_stops_the_export_and_prints_neither_the_expression_nor_the_text(tmp_path, capsys):
    repo = _repo(tmp_path, {"README.md": "row " + _ID + "\n"})
    code, out = _run(tmp_path, repo, ["# c", _ID_RE])
    shown = capsys.readouterr().out
    assert code == 1 and not (out / ".git").exists()
    assert "denylist term" in shown and "line 2" in shown
    assert "ZZ" not in shown and "[0-9]" not in shown


def test_a_re_line_finds_the_text_in_a_commit_message_and_an_old_blob_of_the_history(tmp_path, capsys):
    repo = _history_repo(tmp_path, [({"a.txt": "one\n"}, "built for " + _ID), ({"a.txt": "has " + _ID + "\n"}, "second"),
                                    ({"a.txt": "three\n"}, "third")])
    assert _scan_history(tmp_path, repo, terms=(_ID_RE,)) == 1
    shown = capsys.readouterr().out
    assert "message" in shown and "content" in shown and "ZZ" not in shown


def test_a_re_line_is_applied_to_the_identity_and_the_commit_message(tmp_path, capsys):
    repo = _repo(tmp_path, {"README.md": "hi\n"})
    code, out = _run(tmp_path, repo, ["# c", _ID_RE], "--message", "for " + _ID)
    assert code == 1 and not (out / ".git").exists()
    assert "ZZ" not in capsys.readouterr().out
    assert ep.scan_identity([("author", "Pat " + _ID)], [(2, _ID_RE)]) == [("denylist term", "author", "line 2 of the denylist file")]


@pytest.mark.parametrize("line", ["re:(zzq", "re:(?:zzq)?", "re:", "re:   "])
def test_a_bad_re_line_stops_the_run_by_line_number_without_printing_the_line(tmp_path, line):
    with pytest.raises(SystemExit) as stopped:
        ep.load_terms(_terms(tmp_path, "fine", line))
    assert "line 2 of the denylist file" in str(stopped.value) and "zzq" not in str(stopped.value)


def test_a_plain_line_is_still_plain_text_not_an_expression(tmp_path):
    repo = _repo(tmp_path, {"README.md": "abc and (x)\n"})
    hits = ep.scan(str(_export(tmp_path, repo)), ep.load_terms(_terms(tmp_path, "a.c", "(x)")))
    assert [h[2] for h in hits] == ["line 2 of the denylist file"]  # "a.c" does not match "abc"; "(x)" is text, not a group


def test_the_tool_source_no_longer_holds_a_ticket_prefix_list_or_a_runner_name():
    source = open(ep.__file__, encoding="utf-8").read()
    assert "portfolio ticket id" not in ep.PRIVATE_TRACES and "personal runner name" not in ep.PRIVATE_TRACES
    assert "portfolio ticket id" not in source and "personal runner name" not in source
    assert not re.search(r"\(\?:[A-Z]{2,6}(?:\|[A-Z]{2,6}){2,}\)", source), "an alternation of upper-case prefixes"
    assert not re.search(r"meterline-\"\s*r?\"", source), "a runner name spelled in two halves"
    assert set(ep.PRIVATE_TRACES) == {"backlog ticket id", "private backlog file", "private rules file", "parked-by note",
                                      "process front-matter key"}


def test_ordinary_hyphenated_names_are_not_mistaken_for_ticket_ids_or_runners(tmp_path):
    repo = _repo(tmp_path, {"README.md": "UTF-8, SHA-256, ISO-8601, RFC-3339, the self-hosted runner, meterline-gold-v1\n"})
    assert ep.scan(str(_export(tmp_path, repo)), [(1, "zzz")]) == []


def test_the_real_tree_exports_without_the_admin_spec_the_build_logs_or_process_keys(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ep.INCLUDE[:] = _REAL_INCLUDE
    d = tmp_path / "terms.txt"
    d.write_text("no-such" + "-term-in-the-tree\n")
    out = tmp_path / "public"
    assert ep.main(["--repo", root, "--ref", "HEAD", "--out", str(out), "--denylist", str(d), "--scan-only"]) == 0
    dev = _dev_files(out)
    assert _ADMIN_SPEC not in dev
    assert not [f for f in dev if "/plans/" in f], dev
    for path in out.rglob("*"):
        if path.is_file() and path.suffix in (".md", ".txt", ".yml"):
            text = path.read_text(encoding="utf-8", errors="replace")
            assert _SENS not in text and "meterline-" + "mac" not in text, path


# ---- MF-6: a scan of the whole history, and a gitleaks that is new enough ----

_HIST_TERM = "plant" + "edterm"


def _git_run(repo, *args, **env):
    full = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", **env}
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=full).stdout.strip()


def _history_repo(tmp_path, steps):
    """steps: (files, message[, env]) per commit, on main; a file whose body is None is deleted."""
    r = tmp_path / "hist"
    r.mkdir()
    _git_run(r, "init", "-q", "-b", "main")
    for step in steps:
        files, message, *rest = step
        for rel, body in files.items():
            if body is None:
                (r / rel).unlink()
            else:
                (r / rel).parent.mkdir(parents=True, exist_ok=True)
                (r / rel).write_text(body)
        _git_run(r, "add", "-A")
        _git_run(r, "commit", "-q", "-m", message, **(rest[0] if rest else {}))
    return r


def _scan_history(tmp_path, repo, terms=(_HIST_TERM,)):
    d = tmp_path / "terms.txt"
    d.write_text("# c\n" + "\n".join(terms) + "\n")
    return ep.main(["--scan-history", "--repo", str(repo), "--denylist", str(d)])


def test_a_clean_history_passes_the_history_scan(tmp_path, capsys):
    repo = _history_repo(tmp_path, [({"a.txt": "one\n"}, "first"), ({"a.txt": "two\n"}, "second")])
    assert _scan_history(tmp_path, repo) == 0
    assert "history clean" in capsys.readouterr().out


def test_a_term_only_in_a_commit_message_fails_the_history_scan_and_hides_the_term(tmp_path, capsys):
    repo = _history_repo(tmp_path, [({"a.txt": "one\n"}, "first"), ({"a.txt": "two\n"}, "built for " + _HIST_TERM),
                                    ({"a.txt": "three\n"}, "third")])
    # the tree at HEAD is clean, so the export scan cannot see it; the history scan must
    ep.INCLUDE[:] = ["a.txt"]
    assert ep.scan(str(_export(tmp_path, repo)), [(1, _HIST_TERM)]) == []
    assert _scan_history(tmp_path, repo) == 1
    shown = capsys.readouterr().out
    assert _git_run(repo, "rev-parse", "HEAD~1")[:12] in shown and _HIST_TERM not in shown.lower()
    assert "message" in shown


def test_a_term_only_in_an_old_blob_fails_the_history_scan(tmp_path, capsys):
    repo = _history_repo(tmp_path, [({"a.txt": "one\n"}, "first"), ({"a.txt": "has " + _HIST_TERM + "\n"}, "second"),
                                    ({"a.txt": "clean again\n"}, "third")])
    ep.INCLUDE[:] = ["a.txt"]
    assert ep.scan(str(_export(tmp_path, repo)), [(1, _HIST_TERM)]) == []
    assert _scan_history(tmp_path, repo) == 1
    shown = capsys.readouterr().out
    assert _git_run(repo, "rev-parse", "HEAD~1")[:12] in shown and _HIST_TERM not in shown.lower()
    assert "content" in shown


def test_a_term_only_in_a_path_that_was_later_deleted_fails_the_history_scan(tmp_path, capsys):
    repo = _history_repo(tmp_path, [({"notes-" + _HIST_TERM + ".txt": "n\n"}, "first"), ({"notes-" + _HIST_TERM + ".txt": None}, "second")])
    assert _scan_history(tmp_path, repo) == 1
    shown = capsys.readouterr().out
    assert "path" in shown and _HIST_TERM not in shown.lower()


def test_a_term_in_the_author_identity_fails_the_history_scan(tmp_path, capsys):
    repo = _history_repo(tmp_path, [({"a.txt": "one\n"}, "first", {"GIT_AUTHOR_NAME": "Pat " + _HIST_TERM})])
    assert _scan_history(tmp_path, repo) == 1
    shown = capsys.readouterr().out
    assert "identity" in shown and _HIST_TERM not in shown.lower()


def test_a_term_on_a_branch_other_than_the_checked_out_one_fails_the_history_scan(tmp_path):
    repo = _history_repo(tmp_path, [({"a.txt": "one\n"}, "first")])
    _git_run(repo, "switch", "-q", "-c", "side")
    (repo / "b.txt").write_text(_HIST_TERM + "\n")
    _git_run(repo, "add", "b.txt")
    _git_run(repo, "commit", "-q", "-m", "side work")
    _git_run(repo, "switch", "-q", "main")
    assert _scan_history(tmp_path, repo) == 1


def test_the_history_scan_needs_a_denylist_and_no_out_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("METERLINE_DENYLIST", raising=False)
    repo = _history_repo(tmp_path, [({"a.txt": "one\n"}, "first")])
    with pytest.raises(SystemExit) as e:
        ep.main(["--scan-history", "--repo", str(repo)])
    assert "denylist" in str(e.value.code)


def _fake_gitleaks(tmp_path, monkeypatch, version, scan_exit=0):
    stub = tmp_path / "bin" / "gitleaks"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text("#!/bin/sh\nif [ \"$1\" = version ]; then echo %s; exit 0; fi\nexit %d\n" % (version, scan_exit))
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub.parent) + os.pathsep + os.environ["PATH"])


@pytest.mark.parametrize("version", ["8.29.9", "8.18.4", "7.6.1", "unknown"])
def test_a_gitleaks_older_than_the_minimum_refuses_the_commit(tmp_path, monkeypatch, version):
    _fake_gitleaks(tmp_path, monkeypatch, version)
    repo = _repo(tmp_path, {"README.md": "hi\n"})
    d = tmp_path / "terms.txt"
    d.write_text("zzz\n")
    with pytest.raises(SystemExit) as e:
        ep.main(["--repo", str(repo), "--ref", "HEAD", "--out", str(tmp_path / "out"), "--denylist", str(d),
                 "--author-name", "t", "--author-email", "t@example.com"])
    assert "8.30" in str(e.value.code) and "gitleaks" in str(e.value.code)
    assert not (tmp_path / "out" / ".git").exists()


@pytest.mark.parametrize("version", ["8.30.0", "8.30.1", "8.31.0", "9.0.0"])
def test_a_gitleaks_at_or_above_the_minimum_commits(tmp_path, monkeypatch, version):
    _fake_gitleaks(tmp_path, monkeypatch, version)
    repo = _repo(tmp_path, {"README.md": "hi\n"})
    d = tmp_path / "terms.txt"
    d.write_text("zzz\n")
    code = ep.main(["--repo", str(repo), "--ref", "HEAD", "--out", str(tmp_path / "out"), "--denylist", str(d),
                    "--author-name", "t", "--author-email", "t@example.com"])
    assert code == 0 and (tmp_path / "out" / ".git").exists()


def test_binary_content_with_the_record_separator_byte_does_not_break_the_history_scan(tmp_path, capsys):
    # a gzip or Parquet file in history can hold any byte, 0x1e included; the scan must still name the real commit id
    repo = _history_repo(tmp_path, [({"a.txt": "one\n"}, "first"), ({"b.txt": "two\n"}, "second")])
    (repo / "blob.bin").write_bytes(b"\x00\x1e" + b"\x1e".join([b"abc", b"\xff\xfe", b"zz"]) + b"\x1e\x00")
    _git_run(repo, "add", "blob.bin")
    _git_run(repo, "commit", "-q", "-m", "binary")
    assert _scan_history(tmp_path, repo) == 0
    assert "history clean" in capsys.readouterr().out
    (repo / "blob.bin").write_bytes(b"\x00\x1e" + _HIST_TERM.encode() + b"\x1e\x00")
    _git_run(repo, "add", "blob.bin")
    _git_run(repo, "commit", "-q", "-m", "binary with a term after the separator byte")
    assert _scan_history(tmp_path, repo) == 1
    hits = [l for l in capsys.readouterr().out.splitlines() if l.startswith("HIT")]
    assert hits == ["HIT  content  " + _git_run(repo, "rev-parse", "HEAD")[:12]], hits


def test_a_term_inside_an_old_gzip_file_fails_the_history_scan_and_random_bytes_do_not(tmp_path, capsys):
    import gzip
    repo = _history_repo(tmp_path, [({"a.txt": "one\n"}, "first")])
    (repo / "data.json.gz").write_bytes(gzip.compress(("{\"note\": \"" + _HIST_TERM + "\"}").encode()))
    _git_run(repo, "add", "data.json.gz")
    _git_run(repo, "commit", "-q", "-m", "a gzip file")
    _git_run(repo, "rm", "-q", "data.json.gz")
    _git_run(repo, "commit", "-q", "-m", "gone again")
    assert _scan_history(tmp_path, repo) == 1
    assert "content" in capsys.readouterr().out
    # a term that is only in the gzip header (the stored file name), not in the text, is no hit: only the decompressed
    # text counts, as in the export scan; the bytes of a compressed file match short terms by chance
    import io
    buf = io.BytesIO()
    with gzip.GzipFile(filename="noi" + "sename", mode="wb", fileobj=buf, mtime=0) as fh:
        fh.write(b'{"ok": true}')
    assert b"noi" + b"sename" in buf.getvalue()
    (repo / "noise.json.gz").write_bytes(buf.getvalue())
    _git_run(repo, "add", "noise.json.gz")
    _git_run(repo, "commit", "-q", "-m", "a gzip file with a name in its header")
    assert _scan_history(tmp_path, repo, terms=("noi" + "sename",)) == 0
