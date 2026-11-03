#!/usr/bin/env python3
"""Build the public tree of meterline from a git ref, scan it, and commit it as one squashed commit.

    python3 tools/export_public.py --out /path/to/new-dir --denylist /path/to/terms.txt [--ref origin/main]
    python3 tools/export_public.py --scan-history --denylist /path/to/terms.txt [--repo .] [--since export/v0.1]
    python3 tools/export_public.py --onto /path/to/clone-of-public --tag export/v0.2 --denylist /path/to/terms.txt \\
        --author-name NAME --author-email EMAIL [--allow-divergence]

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

--since REF (only with --scan-history) scans only the commits that are reachable from a ref but not from REF, so a later
release gate covers what the previous export tag does not.

Each public version after the first is a tag `export/vX.Y` on a private commit. --tag NAME checks that the tag exists in
--repo and names the same commit as --ref (--ref defaults to the tag), and ends the commit message with one
`Export-Tag: NAME` line; the first version is exported with it too, so later versions can start from it. --onto CLONE
(with --tag, --author-name and --author-email) makes the export one new commit on top of the history of CLONE, a clone of the
public repository: on a new branch named after the tag, which must not exist yet, with nothing pushed. CLONE must be on a
branch with no uncommitted change. The same scans run first. The run then compares the HEAD of CLONE with its last
export commit (the first commit of `git log HEAD` with an `Export-Tag:` trailer). For each path that the public side changed
since then, the new export must equal what the public repository now holds, which is so once the change has been ported back
to the private repository; otherwise the run lists the paths and stops, because the new commit would silently undo that
change. --allow-divergence overrides it with a warning that lists exactly those paths. Every refusal comes before anything
is written to CLONE.

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
import tempfile
import uuid

INCLUDE = [".gitignore", "README.md", "AGENTS.md", "llms.txt", "examples", "LICENSE", "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md", "pytest.ini", "ruff.toml", ".gitleaks.toml", ".github", "docs", "packages", "tools"]

# Left out of the export even though their directory is allowlisted (tests that read private files).
EXCLUDE = ["tools/test_private_rules.py", "tools/test_commit_messages.py",
           # dependency-update configuration of the working repository only
           ".github/dependabot.yml",
           # the owner's issue-to-pull-request route (a workflow and its issue form) runs on the working repository only
           ".github/workflows/claude.yml", ".github/ISSUE_TEMPLATE/task.yml",
           # the build contract of the integration base: it names backlog rows and the private process that decided it
           "docs/dev/meterline-integration-base-SPEC-v1.0-2026-10-29.md",
           # the build contract of improvements round 1: the same reason
           "docs/dev/meterline-improvements-1-SPEC-v1.0-2026-10-29.md",
           # the build contract of improvements round 2: the same reason
           "docs/dev/meterline-improvements-2-SPEC-v1.0-2026-10-30.md",
           # the build contract of the job package and the folder lock (job 1a): the same reason
           "docs/dev/meterline-job-1a-SPEC-v1.0-2026-10-31.md",
           # the build contract of auto and complete_through (auto 1b): the same reason
           "docs/dev/meterline-auto-1b-SPEC-v1.0-2026-10-31.md",
           # the build contract of dated builds, frozen months and the published folder (publish 1c): the same reason
           "docs/dev/meterline-publish-1c-SPEC-v1.0-2026-10-31.md"]

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
    rewrite_workflows(out)
    return paths


OMIT_BEGIN, OMIT_END = "# export:omit-begin", "# export:omit-end"
RUNNER_SWITCH = re.compile(r"^(\s*runs-on:)\s*\$\{\{.*\bCI_RUNNER\b.*\}\}\s*$")
SELF_HOSTED_IF = re.compile(r"^\s+if: runner\.environment == 'self-hosted'\s*$")
HOSTED_IF = re.compile(r"^\s+if: runner\.environment == 'github-hosted'\s*$")


def public_workflow(text):
    """A workflow of the working repository, as the public repository gets it: no self-hosted runner and no runner variable.

    Every `runs-on` that reads a CI_RUNNER variable becomes `ubuntu-latest`; a step that runs only on a self-hosted runner is
    dropped, and so is the `if` that keeps a step to the hosted ones; the comment lines between `# export:omit-begin` and
    `# export:omit-end` are dropped with the two marker lines. A marker without its partner stops the export."""
    lines, kept, omitting = text.split("\n"), [], False
    for line in lines:
        if line.strip() == OMIT_BEGIN or line.strip() == OMIT_END:
            if omitting == (line.strip() == OMIT_BEGIN):
                raise SystemExit("workflow comment markers %s / %s do not pair up" % (OMIT_BEGIN, OMIT_END))
            omitting = not omitting
        elif not omitting and not HOSTED_IF.match(line):
            kept.append(RUNNER_SWITCH.sub(r"\1 ubuntu-latest", line))
    if omitting:
        raise SystemExit("workflow comment marker %s has no %s" % (OMIT_BEGIN, OMIT_END))
    out, i = [], 0
    while i < len(kept):
        step = re.match(r"^(\s*)- ", kept[i])
        if not step:
            out.append(kept[i])
            i += 1
            continue
        end = i + 1  # the step ends at the next line that is not indented deeper than its dash
        while end < len(kept) and (not kept[end].strip() or len(kept[end]) - len(kept[end].lstrip()) > len(step.group(1))):
            end += 1
        core = end
        while core > i + 1 and not kept[core - 1].strip():  # blank lines after a step belong to what follows
            core -= 1
        out.extend(kept[core:end] if any(SELF_HOSTED_IF.match(b) for b in kept[i:core]) else kept[i:end])
        i = end
    return "\n".join(out)


def rewrite_workflows(root):
    folder = os.path.join(root, ".github", "workflows")
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if name.endswith((".yml", ".yaml")):
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(public_workflow(text))


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


def scan_history(repo, terms, since=None):
    """Hits (kind, commit id, detail) for the denylist terms anywhere in the history of any ref: message, identity,
    path or file content (reported at the first commit that holds that version). Terms are never reported, not even by
    line number. With `since` (a commit id), only the commits reachable from a ref but not from `since` are scanned."""
    not_since = ["^" + since] if since else []
    term_rx = [term_pattern(t) for _n, t in terms]

    def found(text):
        return any(rx.search(text) for rx in term_rx)

    def log(*args):
        return subprocess.run(["git", "--no-optional-locks", "log", "--all", "--no-color", *not_since, *args], cwd=repo, check=True,
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


TRAILER = "Export-Tag"
FIRST_MESSAGE = "meterline: first public version"


def git_ok(args, cwd, **kw):
    """A git command that may fail: the completed process, never an exception."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, **kw)


def resolve_commit(repo, name):
    """The full commit id that `name` resolves to in `repo`, or None."""
    done = git_ok(["rev-parse", "--verify", "--quiet", "--end-of-options", name + "^{commit}"], repo, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def check_tag(repo, tag, ref):
    """Refuse unless `tag` is a tag of `repo` that names the same commit as `ref`."""
    if git_ok(["check-ref-format", "--branch", tag], repo).returncode != 0:
        raise SystemExit("--tag %s cannot be used as a branch name" % tag)
    tagged = resolve_commit(repo, "refs/tags/" + tag)
    if tagged is None:
        raise SystemExit("--tag %s is not a tag of %s: create it on the commit to export first" % (tag, os.path.abspath(repo)))
    commit = resolve_commit(repo, ref)
    if commit is None:
        raise SystemExit("--ref %s does not name a commit of %s" % (ref, os.path.abspath(repo)))
    if commit != tagged:
        raise SystemExit("--ref %s and the tag %s name different commits: export the commit that the tag names" % (ref, tag))


def with_trailer(message, tag):
    """`message` followed by one `Export-Tag:` line (it ends with that line and one newline)."""
    if re.search(r"(?im)^%s:" % TRAILER, message):
        raise SystemExit("--message holds an %s: line of its own: the tool adds that line from --tag" % TRAILER)
    return message.rstrip() + "\n\n%s: %s\n" % (TRAILER, tag)


def parse_ls_tree(raw):
    """{path: (mode, object id)} from the output of `git ls-tree -r -z`."""
    entries = {}
    for row in raw.decode("utf-8", "replace").split("\0"):
        if row:
            info, path = row.split("\t", 1)
            mode, _kind, oid = info.split()
            entries[path] = (mode, oid)
    return entries


def tree_entries(root, object_format="sha1"):
    """{path: (mode, object id)} of the tree that git builds from the folder `root`, in a throwaway repository (the
    .gitignore of the folder counts, as it does when the export is committed)."""
    with tempfile.TemporaryDirectory() as tmp:
        gitdir = os.path.join(tmp, "g")
        env = {**os.environ, "GIT_DIR": gitdir, "GIT_WORK_TREE": root, "GIT_INDEX_FILE": os.path.join(tmp, "index")}
        git(["init", "-q", "-b", "main", "--object-format=" + object_format], root, env=env)
        git(["add", "-A"], root, env=env)
        tree = git(["write-tree"], root, env=env, text=True).stdout.strip()
        return parse_ls_tree(git(["ls-tree", "-r", "-z", tree], root, env=env).stdout)


def check_clone(clone, tag):
    """Refuse (plain sentence) unless `clone` is the top level of a git repository, on a branch that has a commit, with no
    uncommitted change and no branch of the name `tag`. Reads only."""
    def read(*args):
        return git_ok(["--no-optional-locks", *args], clone, text=True)
    top = read("rev-parse", "--show-toplevel") if os.path.isdir(clone) else None
    if top is None or top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(clone):
        raise SystemExit("--onto %s is not the top level of a git repository: give the folder of a clone of the public repository" % clone)
    if read("symbolic-ref", "-q", "HEAD").returncode != 0:
        raise SystemExit("--onto %s is not on a branch (detached HEAD): switch to the branch to build on" % clone)
    if read("rev-parse", "--verify", "--quiet", "HEAD").returncode != 0:
        raise SystemExit("--onto %s has no commit yet: export the first public version without --onto" % clone)
    if read("status", "--porcelain").stdout.strip():
        raise SystemExit("--onto %s has uncommitted changes (modified or untracked files): commit, stash or remove them first" % clone)
    parts = tag.split("/")
    blocking = ["refs/heads/" + "/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    taken = [r for r in blocking if read("show-ref", "--verify", "--quiet", r).returncode == 0]
    taken += read("for-each-ref", "--format=%(refname)", "refs/remotes/*/" + tag).stdout.split()
    if taken:
        raise SystemExit("the branch %s already exists in %s (%s): the new commit goes on a new branch named after the tag"
                         % (tag, clone, ", ".join(taken)))


def last_export_commit(clone):
    """The id of the first commit of `git log HEAD` that carries an `Export-Tag:` trailer, or None."""
    fmt = "%H%x00%(trailers:key=" + TRAILER + ",valueonly,separator=%x1f)%x00"
    tokens = git(["--no-optional-locks", "log", "HEAD", "--format=" + fmt], clone).stdout.decode("utf-8", "replace").split("\0")
    for i in range(0, len(tokens) - 1, 2):
        values = [v.strip() for v in tokens[i + 1].split("\x1f") if v.strip()]
        if len(values) > 1:
            raise SystemExit("commit %s of the public clone carries more than one %s: line" % (tokens[i].strip()[:12], TRAILER))
        if values:
            return tokens[i].strip()
    return None


def report_scan(paths, ref, root, terms, hits):
    """Print the summary line and the hits; False (after FAILED) when there is a hit."""
    n_files = sum(len(f) for _, _, f in os.walk(root))
    print("exported %s from %s: %d files; scanned against %d terms" % (", ".join(paths), ref, n_files, len(terms)))
    for kind, rel, detail in hits:
        print("HIT  %s  %s  %s" % (kind, rel, detail))
    if hits:
        print("FAILED: %d hit(s); no commit made" % len(hits))
        return False
    print("scan clean")
    return True


def export_onto(a, message):
    """Commit the export of a.ref as one new commit on top of HEAD of the clone a.onto, on a new branch named after a.tag.
    Every refusal comes before the first write to the clone. Returns the exit status."""
    clone = os.path.abspath(a.onto)
    check_clone(clone, a.tag)
    check_tag(a.repo, a.tag, a.ref)
    with tempfile.TemporaryDirectory() as tmp:
        new = os.path.join(tmp, "new")
        os.mkdir(new)
        paths = export_tree(a.repo, a.ref, new)
        terms = load_terms(a.denylist)
        if not terms:
            raise SystemExit("the denylist file holds no terms")
        hits = scan(new, terms) + scan_identity([("author/committer name", a.author_name), ("author/committer email", a.author_email),
                                                  ("commit message", message)], terms)
        if not report_scan(paths, a.ref, new, terms, hits):
            return 1
        if not a.no_gitleaks:
            if not run_gitleaks(new):
                print("FAILED: gitleaks found a leak in the tree; no commit made")
                return 1
            print("gitleaks clean")
        fmt = git(["--no-optional-locks", "rev-parse", "--show-object-format"], clone, text=True).stdout.strip()
        head = parse_ls_tree(git(["--no-optional-locks", "ls-tree", "-r", "-z", "HEAD"], clone).stdout)
        last_commit = last_export_commit(clone)
        if last_commit is None:
            raise SystemExit("no commit of %s carries an %s: trailer, so there is no last export to compare with: export the first "
                             "public version with --tag (for example --tag export/v0.1) and publish that" % (clone, TRAILER))
        last = parse_ls_tree(git(["--no-optional-locks", "ls-tree", "-r", "-z", last_commit], clone).stdout)
        entries = tree_entries(new, fmt)
        # a path the public side changed since the last export must already be in the new export (ported back)
        changed = [p for p in set(last) | set(head) if last.get(p) != head.get(p)]
        diverged = sorted(p for p in changed if entries.get(p) != head.get(p))
        if diverged and not a.allow_divergence:
            for path in diverged:
                print("DIVERGED  " + path)
            print("The public repository holds changes that are not in the new export: a contribution merged there and not "
                  "yet ported back. Port them back to the private repository first (see \"Later public versions\" in "
                  "docs/dev/RELEASING.md); no commit made.")
            return 1
        if diverged:
            print("WARNING: --allow-divergence: the new commit overwrites these paths of the public repository:")
            for path in diverged:
                print("  " + path)
        if entries == head:
            raise SystemExit("nothing to export: the tree of %s equals the tree at HEAD of %s" % (a.tag, clone))
        return write_onto(a, clone, new, tmp, entries, message)


def write_onto(a, clone, new, tmp, entries, message):
    """The writes to the clone: the tree from a temporary index, one commit on top of HEAD, a new branch on it."""
    env = {**os.environ, "GIT_AUTHOR_NAME": a.author_name, "GIT_AUTHOR_EMAIL": a.author_email,
           "GIT_COMMITTER_NAME": a.author_name, "GIT_COMMITTER_EMAIL": a.author_email}
    try:
        gitdir = git(["rev-parse", "--absolute-git-dir"], clone, text=True).stdout.strip()
        index = {**env, "GIT_INDEX_FILE": os.path.join(tmp, "index-onto")}
        git(["--git-dir", gitdir, "--work-tree", new, "add", "-A"], new, env=index)
        tree = git(["--git-dir", gitdir, "write-tree"], clone, env=index, text=True).stdout.strip()
        if parse_ls_tree(git(["--git-dir", gitdir, "ls-tree", "-r", "-z", tree], clone).stdout) != entries:
            raise SystemExit("the tree built in %s differs from the scanned export (a git setting of the clone changes what is "
                             "added); no branch made" % clone)
        parent = git(["rev-parse", "HEAD"], clone, text=True).stdout.strip()
        commit = git(["commit-tree", tree, "-p", parent, "-F", "-"], clone, env=env, input=message.encode("utf-8"),
                     text=False).stdout.decode().strip()
        git(["switch", "-q", "-c", a.tag, commit], clone)
    except subprocess.CalledProcessError as failed:
        raise SystemExit("git %s failed in %s: %s" % (failed.cmd[1], clone, failed.stderr.decode("utf-8", "replace").strip())) from None
    print("committed one commit on branch %s in %s, on top of %s (nothing pushed)" % (a.tag, clone, parent[:12]))
    return 0


def commit_tree(out, name, email, message):
    env = {**os.environ, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=out, check=True, env=env)
    subprocess.run(["git", "add", "--", *[x for x in sorted(os.listdir(out)) if x != ".git"]], cwd=out, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=out, check=True, env=env)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="a new or empty directory for the public tree (required unless --scan-history or --onto)")
    ap.add_argument("--denylist", default=os.environ.get("METERLINE_DENYLIST"),
                    help="file of terms, one per line, kept outside the repo (or env METERLINE_DENYLIST)")
    ap.add_argument("--ref", default=None, help="the commit to export (default: --tag when given, else origin/main)")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--scan-only", action="store_true", help="export and scan, make no commit")
    ap.add_argument("--scan-history", action="store_true",
                    help="search every commit of every ref of --repo for the denylist terms; prints kinds and commit ids only")
    ap.add_argument("--since", help="with --scan-history: scan only the commits not reachable from this ref (the previous export tag)")
    ap.add_argument("--tag", help="the export tag, for example export/v0.2: it must name the same commit as --ref; the commit message "
                                  "ends with an Export-Tag line")
    ap.add_argument("--onto", help="a clone of the public repository: make the export one new commit on a new branch named after "
                                   "--tag, on top of its history (needs --tag, --author-name and --author-email)")
    ap.add_argument("--allow-divergence", action="store_true",
                    help="with --onto: go on although the public repository differs from the last export (overwrites those paths)")
    ap.add_argument("--author-name", help="required for a commit; the owner's name or a noreply identity")
    ap.add_argument("--author-email", help="required for a commit; a noreply address is allowed")
    ap.add_argument("--no-gitleaks", action="store_true", help="commit without running gitleaks on the tree")
    ap.add_argument("--message", default=None, help="the commit message (default: \"%s\", or \"meterline: public version vX.Y\" "
                                                    "with --onto)" % FIRST_MESSAGE)
    a = ap.parse_args(argv)
    if a.since and not a.scan_history:
        raise SystemExit("--since only works with --scan-history")
    if a.allow_divergence and not a.onto:
        raise SystemExit("--allow-divergence only works with --onto")
    if a.scan_history and a.tag:
        raise SystemExit("--tag does not apply to --scan-history")
    if a.onto:
        for flag, given in (("--out", a.out), ("--scan-only", a.scan_only), ("--scan-history", a.scan_history)):
            if given:
                raise SystemExit("--onto cannot be combined with %s: it makes its commit in the clone, not in a new folder" % flag)
        for flag, given in (("--tag", a.tag), ("--author-name", a.author_name), ("--author-email", a.author_email)):
            if not given:
                raise SystemExit("%s is required with --onto" % flag)
    if not a.denylist:
        raise SystemExit("--denylist (or METERLINE_DENYLIST) is required: the scan does not run without it")
    if a.scan_history:
        terms = load_terms(a.denylist)
        if not terms:
            raise SystemExit("the denylist file holds no terms")
        since = None
        if a.since:
            since = resolve_commit(a.repo, a.since)
            if since is None:
                raise SystemExit("--since %s does not name a commit of %s" % (a.since, os.path.abspath(a.repo)))
        hits, n_commits = scan_history(a.repo, terms, since)
        if since:
            print("history scan: %d commits on all refs of %s not reachable from %s; scanned against %d terms"
                  % (n_commits, os.path.abspath(a.repo), a.since, len(terms)))
        else:
            print("history scan: %d commits on all refs of %s; scanned against %d terms" % (n_commits, os.path.abspath(a.repo), len(terms)))
        for kind, sha, _detail in hits:
            print("HIT  %s  %s" % (kind, sha))
        if hits:
            print("FAILED: %d hit(s) in %d commit(s)" % (len(hits), len({sha for _k, sha, _d in hits})))
            return 1
        print("history clean")
        return 0
    a.ref = a.ref or a.tag or "origin/main"
    message = a.message
    if message is None:
        message = ("meterline: public version " + a.tag.split("/", 1)[-1]) if a.onto else FIRST_MESSAGE
    if a.tag:
        message = with_trailer(message, a.tag)
    if a.onto:
        return export_onto(a, message)
    if not a.out:
        raise SystemExit("--out is required (unless --scan-history)")
    if not a.scan_only:
        if not a.author_name:
            raise SystemExit("--author-name is required (unless --scan-only): the commit identity is never taken from git config")
        if not a.author_email:
            raise SystemExit("--author-email is required (unless --scan-only): the commit identity is never taken from git config")
    if a.tag:
        check_tag(a.repo, a.tag, a.ref)
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
                               ("commit message", message)], terms)
    if not report_scan(paths, a.ref, a.out, terms, hits):
        return 1
    if a.scan_only:
        return 0
    if not a.no_gitleaks:
        if not run_gitleaks(a.out):
            print("FAILED: gitleaks found a leak in the tree; no commit made")
            return 1
        print("gitleaks clean")
    commit_tree(a.out, a.author_name, a.author_email, message)
    print("committed one squashed commit in", a.out, "(nothing pushed; no remote set)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
