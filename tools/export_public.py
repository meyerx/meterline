#!/usr/bin/env python3
"""Build the public tree of meterline from a git ref, scan it, and commit it as one squashed commit.

    python3 tools/export_public.py --out /path/to/new-dir --denylist /path/to/terms.txt [--ref origin/main]
    python3 tools/export_public.py --scan-history --denylist /path/to/terms.txt [--repo .]

The tree is built from an allowlist (INCLUDE below), taken from committed files only, so nothing from a
working directory leaks in. Files outside the allowlist (the session rules file, the backlog, this tool)
are never exported. The tree is then scanned; any hit stops the run before a commit is made:

  - secret patterns (cloud and provider keys, tokens, private keys, bearer strings)
  - any email address whose domain is not example.com/.org/.net
  - absolute home paths (/Users/<name>/, /home/<name>/)
  - private working traces: backlog ticket ids (METR-n, this repository's own prefix), the backlog and rules file
    names, notes of who parked a task, the process keys of a front-matter block
  - every term of the denylist file, case-insensitive. The file stays outside the repo; its terms are
    never printed (hits show the term's line number only). A line that starts with `re:` is a regular expression
    (case-insensitive, searched in every file, path, message and identity like a plain term); any other line is plain
    text. The owner's private ticket prefixes, runner and host names are `re:` or plain lines of that file, never code
    of this repository.

docs/dev leaves the export except the files that a public document links (a markdown link or a docs/dev/ path, in an
exported .md, .txt or .yml file outside docs/dev); a public document that links a docs/dev file that is left out fails
the scan, and so does a kept docs/dev file that no public document links. The process keys of a front-matter block
(PROCESS_KEYS) are dropped from every exported markdown file, so a frozen spec ships without them.

The control is the squashed single commit: the public repository starts from one commit built from this
tree, so the private history is never published. The run therefore refuses (non-zero status) when an INCLUDE
path is missing from the ref, requires --author-name and --author-email for a commit (the identity and the
commit message are scanned against the denylist too), and runs gitleaks on the tree before committing (it
refuses when gitleaks is absent or older than MIN_GITLEAKS unless --no-gitleaks is given). The limits of the scan are in
docs/dev/RELEASING.md.

--scan-history searches every commit of every ref of the repository for the denylist terms, in commit messages, author
and committer identities, changed paths and the content of every version of every file (read as the export reads a
file: a gzip file once), and prints the kind and the commit id of each hit,
never a term. It exits 1 on a hit. The export scan above reads one tree, so it cannot see a term that only a commit
message or a deleted file ever held; this is the check for a route that would publish the working history.

Nothing is pushed and no remote is set. The owner creates the public repository and pushes.
"""
import argparse
import gzip
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import uuid

INCLUDE = [".gitignore", "README.md", "AGENTS.md", "llms.txt", "examples", "LICENSE", "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md", "pytest.ini", "ruff.toml", ".gitleaks.toml", ".github", "docs", "packages", "tools"]

# Left out of the export even though their directory is allowlisted (tests that read private files).
EXCLUDE = ["tools/test_private_rules.py", "tools/test_commit_messages.py",
           # the build contract of the integration base: it names backlog rows and the private process that decided it
           "docs/dev/meterline-integration-base-SPEC-v1.0-2026-10-29.md",
           # the build contract of improvements round 1: the same reason
           "docs/dev/meterline-improvements-1-SPEC-v1.0-2026-10-29.md",
           # the build contract of improvements round 2: the same reason
           "docs/dev/meterline-improvements-2-SPEC-v1.0-2026-10-30.md"]

MIN_GITLEAKS = (8, 30, 0)  # older gitleaks ignores [[allowlists]] in .gitleaks.toml and reports false hits
DEV = "docs/dev/"
# Front-matter keys that record the owner's working process; dropped from every exported markdown file.
PROCESS_KEYS = ("sensitivity", "accepted_when", "raised_by", "rows", "bet")
# Files whose links decide what stays of docs/dev: documents, not code (a test names a path to assert on it).
LINK_SUFFIXES = (".md", ".txt", ".yml")

SECRET_PATTERNS = {
    "aws key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "github token": r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|github_pat_[A-Za-z0-9_]{30,}",
    "anthropic key": r"sk-ant-[A-Za-z0-9_\-]{20,}",
    "openai-style key": r"\bsk-[A-Za-z0-9]{32,}\b",
    "slack token": r"\bxox[abprs]-[A-Za-z0-9\-]{10,}",
    "google key": r"\bAIza[0-9A-Za-z_\-]{35}\b",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "jwt": r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
    "bearer string": r"[Bb]earer\s+[A-Za-z0-9_\-\.=]{24,}",
    "quoted secret": r"(?i)\b(?:api[_-]?key|secret|token|passwd|password)\b[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_\-\.\/+=]{20,}[\"']",
}
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
OK_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
HOME_PATH = re.compile(r"/(?:Users|home)/[A-Za-z0-9_.\-]+/")
PRIVATE_TRACES = {
    "backlog ticket id": r"METR-?[0-9]+",
    "private backlog file": r"BACKLOG" r"-std",
    "private rules file": r"CLAUDE\.md",
    "parked-by note": r"(?i)\bparked by\b",
    "process front-matter key": r"(?m)^(?:sensitivity|accepted_when|raised_by):",
}
DEV_PATH = re.compile(r"docs/dev/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:md|txt)")
MD_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)|^\[[^\]]+\]:\s*(\S+)", re.M)
FENCE = re.compile(r"```.*?```", re.S)


def git(args, cwd, **kw):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, **kw)


def export_tree(repo, ref, out):
    """Extract the allowlisted paths of `ref` into `out` (committed files only)."""
    listed = git(["ls-tree", "--name-only", ref], repo, text=True).stdout.split("\n")
    missing = [p for p in INCLUDE if p not in listed]
    if missing:
        raise SystemExit("INCLUDE path missing from %s: %s (refusing rather than skipping it)" % (ref, ", ".join(missing)))
    paths = list(INCLUDE)
    raw = git(["archive", "--format=tar", ref, "--", *paths, *[":(exclude)" + x for x in EXCLUDE]], repo).stdout
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        for m in tar.getmembers():
            name = os.path.normpath(m.name)
            if name.startswith(("..", "/")) or not (m.isfile() or m.isdir()):
                raise SystemExit("refusing archive member: " + m.name)
            dest = os.path.join(out, name)
            if m.isdir():
                os.makedirs(dest, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(tar.extractfile(m).read())
    prune_dev(out)
    strip_front_matter_keys(out)
    return paths


def strip_process_keys(text):
    """Drop the PROCESS_KEYS lines (and their indented continuation lines) from a leading front-matter block."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    kept, skipping = [], False
    for line in text[4:end].split("\n"):
        key = re.match(r"([A-Za-z_][\w-]*):", line)
        if key:
            skipping = key.group(1) in PROCESS_KEYS
        elif line and not line[0].isspace():
            skipping = False
        if not skipping:
            kept.append(line)
    return "---\n" + "\n".join(kept) + text[end:]


def dev_references(rel, text):
    """The docs/dev files that the document `rel` (a path under the tree root) links, as paths under the tree root."""
    found = set(DEV_PATH.findall(text))
    for m in MD_LINK.finditer(FENCE.sub("", text)):
        target = (m.group(1) or m.group(2) or "").split("#")[0]
        if not target or "://" in target or target.startswith(("/", "mailto:")):
            continue
        resolved = os.path.normpath(os.path.join(os.path.dirname(rel), target)).replace(os.sep, "/")
        if resolved.startswith(DEV):
            found.add(resolved)
    return found


def _tree_files(root):
    return sorted(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/") for d, _, fs in os.walk(root) for f in fs)


def _read_text(root, rel):
    with open(os.path.join(root, rel), "rb") as fh:
        return fh.read().decode("utf-8", "replace")


def _linking_documents(files, in_dev):
    return [f for f in files if f.endswith(LINK_SUFFIXES) and f.startswith(DEV) == in_dev]


def prune_dev(root):
    """Remove every docs/dev file that no exported document outside docs/dev links."""
    files = _tree_files(root)
    linked = set()
    for rel in _linking_documents(files, in_dev=False):
        linked |= dev_references(rel, _read_text(root, rel))
    for rel in files:
        if rel.startswith(DEV) and rel not in linked:
            os.remove(os.path.join(root, rel))
    for dirpath, _dirs, _files in os.walk(os.path.join(root, DEV), topdown=False):
        if not os.listdir(dirpath):
            os.rmdir(dirpath)


def strip_front_matter_keys(root):
    for rel in _tree_files(root):
        if rel.endswith(".md"):
            path = os.path.join(root, rel)
            with open(path, "rb") as fh:
                raw = fh.read()
            text = raw.decode("utf-8", "replace")
            stripped = strip_process_keys(text)
            if stripped != text:
                with open(path, "wb") as fh:
                    fh.write(stripped.encode("utf-8"))


def check_dev_links(root):
    """Hits for the two link rules: a public document must not link a docs/dev file that is missing from the tree,
    and a docs/dev file that is in the tree must be linked from a document outside docs/dev."""
    hits, files = [], _tree_files(root)
    present, linked = set(files), set()
    for rel in _linking_documents(files, in_dev=False):
        refs = dev_references(rel, _read_text(root, rel))
        linked |= refs
        hits += [("link to a docs/dev file left out of the export", rel, target) for target in sorted(refs - present)]
    for rel in _linking_documents(files, in_dev=True):
        hits += [("link to a docs/dev file left out of the export", rel, target)
                 for target in sorted(dev_references(rel, _read_text(root, rel)) - present - {rel})]
    hits += [("docs/dev file no public document links", rel, "") for rel in files if rel.startswith(DEV) and rel not in linked]
    return hits


REGEX_PREFIX = "re:"


def term_pattern(text):
    """The case-insensitive pattern of one term: `re:<regular expression>` as written, anything else as plain text."""
    if text.startswith(REGEX_PREFIX):
        return re.compile(text[len(REGEX_PREFIX):].strip(), re.I)
    return re.compile(re.escape(text), re.I)


def load_terms(path):
    """The (line number, term) pairs of a denylist file. A `re:` line must be a regular expression that does not match
    the empty text; a line that is not says so by line number only, never by its content."""
    with open(path, encoding="utf-8") as fh:
        terms = [(n, l.strip()) for n, l in enumerate(fh, 1) if l.strip() and not l.lstrip().startswith("#")]
    for n, text in terms:
        if text.startswith(REGEX_PREFIX):
            try:
                empty = term_pattern(text).search("") is not None or not text[len(REGEX_PREFIX):].strip()
            except re.error:
                raise SystemExit("line %d of the denylist file starts with re: but is not a valid regular expression "
                                 "(the line is not printed)" % n) from None
            if empty:
                raise SystemExit("line %d of the denylist file starts with re: but its expression is empty or matches "
                                 "the empty text, so it would hit every file" % n)
    return terms


def scan(root, terms):
    """Return a list of (kind, relative path, detail). Terms are reported by line number only."""
    hits = check_dev_links(root)
    secret_rx = {k: re.compile(v) for k, v in SECRET_PATTERNS.items()}
    trace_rx = {k: re.compile(v) for k, v in PRIVATE_TRACES.items()}
    term_rx = [(n, term_pattern(t)) for n, t in terms]
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root)
            with open(full, "rb") as fh:
                body = fh.read()
            if body[:2] == b"\x1f\x8b":
                try:
                    body = gzip.decompress(body)
                except OSError:
                    pass
            text = body.decode("utf-8", "replace")
            for kind, rx in secret_rx.items():
                if rx.search(text):
                    hits.append((kind, rel, ""))
            for m in EMAIL.finditer(text):
                if not m.group(1).lower().endswith(OK_EMAIL_DOMAINS):
                    hits.append(("email outside example domains", rel, ""))
                    break
            if HOME_PATH.search(text):
                hits.append(("absolute home path", rel, ""))
            for kind, rx in trace_rx.items():
                if rx.search(text) or rx.search(rel):
                    hits.append((kind, rel, ""))
            for n, rx in term_rx:
                if rx.search(text) or rx.search(rel):
                    hits.append(("denylist term", rel, "line %d of the denylist file" % n))
    return hits


def scan_identity(fields, terms):
    """Scan the author/committer identity and the commit message against the denylist (line numbers only)."""
    hits = []
    for label, value in fields:
        for n, t in terms:
            if term_pattern(t).search(value):
                hits.append(("denylist term", label, "line %d of the denylist file" % n))
    return hits


def gitleaks_version(exe):
    """The (major, minor, patch) that `gitleaks version` prints, or None when it prints no such number."""
    try:
        said = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", said)
    return tuple(int(x) for x in m.groups()) if m else None


def run_gitleaks(out):
    """Scan the exported tree with gitleaks. Returns True when clean; refuses when gitleaks is absent or too old."""
    exe = shutil.which("gitleaks")
    if not exe:
        raise SystemExit("gitleaks is not on PATH: install it, or pass --no-gitleaks to commit without it")
    version = gitleaks_version(exe)
    if version is None or version < MIN_GITLEAKS:
        raise SystemExit("gitleaks %s is older than %s (or its version is unreadable): older versions ignore the allowlists "
                         "in .gitleaks.toml; install 8.30.1" % (".".join(map(str, version)) if version else "?",
                                                                ".".join(map(str, MIN_GITLEAKS))))
    return subprocess.run([exe, "dir", "--no-banner", "--redact", out]).returncode == 0


def scan_history(repo, terms):
    """Hits (kind, commit id, detail) for the denylist terms anywhere in the history of any ref: message, identity,
    path or file content (reported at the first commit that holds that version). Terms are never reported, not even by
    line number."""
    term_rx = [term_pattern(t) for _n, t in terms]

    def found(text):
        return any(rx.search(text) for rx in term_rx)

    def log(*args):
        return subprocess.run(["git", "--no-optional-locks", "log", "--all", "--no-color", *args], cwd=repo, check=True,
                              capture_output=True).stdout.decode("utf-8", "replace")

    # a record separator that file content cannot hold by accident: a gzip or Parquet file can hold any byte
    mark = "\x00@@%s@@\x00" % uuid.uuid4().hex
    hits, commits = [], set()
    for record in log("--format=" + mark.replace("\x00", "%x00") + "%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B").split(mark)[1:]:
        sha, *identity, message = record.split("\x00", 5)
        commits.add(sha)
        if found("\n".join(identity)):
            hits.append(("identity", sha[:12], ""))
        if found(message):
            hits.append(("message", sha[:12], ""))
    for record in log("--name-only", "--format=" + mark.replace("\x00", "%x00") + "%H").split(mark)[1:]:
        sha, _, paths = record.partition("\n")
        if found(paths):
            hits.append(("path", sha[:12], ""))
    first_commit = {}
    for record in log("--raw", "--no-renames", "--no-abbrev", "--format=" + mark.replace("\x00", "%x00") + "%H").split(mark)[1:]:
        sha, _, rows = record.partition("\n")
        for row in rows.splitlines():
            fields = row.split(None, 5)  # ":100644 100644 <old> <new> M\t<path>"
            if row.startswith(":") and len(fields) >= 5 and fields[1] != "160000" and set(fields[3]) != {"0"}:
                first_commit.setdefault(fields[3], sha)
    # every version of every file that was ever committed, once, read as the export scan reads a file (gzip once)
    blobs = sorted(first_commit)
    for i in range(0, len(blobs), 200):
        batch = blobs[i:i + 200]
        out = subprocess.run(["git", "--no-optional-locks", "cat-file", "--batch"], cwd=repo, check=True, capture_output=True,
                             input=("\n".join(batch) + "\n").encode()).stdout
        at = 0
        for blob in batch:
            end = out.index(b"\n", at)
            header = out[at:end].split()
            if len(header) != 3:  # "<id> missing"
                at = end + 1
                continue
            size = int(header[2])
            body = out[end + 1:end + 1 + size]
            at = end + 1 + size + 1
            if body[:2] == b"\x1f\x8b":
                try:
                    body = gzip.decompress(body)
                except OSError:
                    pass
            if found(body.decode("utf-8", "replace")):
                hits.append(("content", first_commit[blob][:12], ""))
    return hits, len(commits)


def commit_tree(out, name, email, message):
    env = {**os.environ, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=out, check=True, env=env)
    subprocess.run(["git", "add", "--", *[x for x in sorted(os.listdir(out)) if x != ".git"]], cwd=out, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=out, check=True, env=env)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="a new or empty directory for the public tree (required unless --scan-history)")
    ap.add_argument("--denylist", default=os.environ.get("METERLINE_DENYLIST"),
                    help="file of terms, one per line, kept outside the repo (or env METERLINE_DENYLIST)")
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--scan-only", action="store_true", help="export and scan, make no commit")
    ap.add_argument("--scan-history", action="store_true",
                    help="search every commit of every ref of --repo for the denylist terms; prints kinds and commit ids only")
    ap.add_argument("--author-name", help="required for a commit; the owner's name or a noreply identity")
    ap.add_argument("--author-email", help="required for a commit; a noreply address is allowed")
    ap.add_argument("--no-gitleaks", action="store_true", help="commit without running gitleaks on the tree")
    ap.add_argument("--message", default="meterline: first public version")
    a = ap.parse_args(argv)
    if not a.denylist:
        raise SystemExit("--denylist (or METERLINE_DENYLIST) is required: the scan does not run without it")
    if a.scan_history:
        terms = load_terms(a.denylist)
        if not terms:
            raise SystemExit("the denylist file holds no terms")
        hits, n_commits = scan_history(a.repo, terms)
        print("history scan: %d commits on all refs of %s; scanned against %d terms" % (n_commits, os.path.abspath(a.repo), len(terms)))
        for kind, sha, _detail in hits:
            print("HIT  %s  %s" % (kind, sha))
        if hits:
            print("FAILED: %d hit(s) in %d commit(s)" % (len(hits), len({sha for _k, sha, _d in hits})))
            return 1
        print("history clean")
        return 0
    if not a.out:
        raise SystemExit("--out is required (unless --scan-history)")
    if not a.scan_only:
        if not a.author_name:
            raise SystemExit("--author-name is required (unless --scan-only): the commit identity is never taken from git config")
        if not a.author_email:
            raise SystemExit("--author-email is required (unless --scan-only): the commit identity is never taken from git config")
    if os.path.exists(a.out) and os.listdir(a.out):
        raise SystemExit("--out must be a new or empty directory: " + a.out)
    os.makedirs(a.out, exist_ok=True)
    paths = export_tree(a.repo, a.ref, a.out)
    terms = load_terms(a.denylist)
    if not terms:
        raise SystemExit("the denylist file holds no terms")
    hits = scan(a.out, terms)
    if not a.scan_only:
        hits += scan_identity([("author/committer name", a.author_name), ("author/committer email", a.author_email),
                               ("commit message", a.message)], terms)
    n_files = sum(len(f) for _, _, f in os.walk(a.out))
    print("exported %s from %s: %d files; scanned against %d terms" % (", ".join(paths), a.ref, n_files, len(terms)))
    for kind, rel, detail in hits:
        print("HIT  %s  %s  %s" % (kind, rel, detail))
    if hits:
        print("FAILED: %d hit(s); no commit made" % len(hits))
        return 1
    print("scan clean")
    if a.scan_only:
        return 0
    if not a.no_gitleaks:
        if not run_gitleaks(a.out):
            print("FAILED: gitleaks found a leak in the tree; no commit made")
            return 1
        print("gitleaks clean")
    commit_tree(a.out, a.author_name, a.author_email, a.message)
    print("committed one squashed commit in", a.out, "(nothing pushed; no remote set)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
