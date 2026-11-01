"""F-36: a superseded spec or plan says so. When a document in docs/ names an
earlier one in ``supersedes:``, the earlier one carries ``status: superseded``
and a ``superseded_by:`` line naming the later one, so nobody builds against a
text that has been replaced (L1 rule: a correction banners what it supersedes).
Only the front matter is checked; the body of an accepted spec stays frozen."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def front_matter(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    block = text[4:text.find("\n---", 4)]
    fields = {}
    for line in block.splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith((" ", "\t")):
            fields[key.strip()] = value.strip()
    return fields


def _named_file(value):
    """The file name a ``supersedes:``/``superseded_by:`` value starts with."""
    match = re.match(r"(?:docs/)?(?:dev/)?(?:history/|plans/)?([\w.-]+\.md)", value)
    return match.group(1) if match else None


def superseded_problems(docs_dir):
    files = {p.name: p for p in sorted(Path(docs_dir).rglob("*.md"))}
    problems = []
    for name, path in files.items():
        older = _named_file(front_matter(path).get("supersedes", ""))
        if older is None:
            continue
        if older not in files:
            problems.append("%s supersedes %s, which does not exist" % (name, older))
            continue
        meta = front_matter(files[older])
        if meta.get("status") != "superseded":
            problems.append("%s is superseded by %s but its status is %r" % (older, name, meta.get("status")))
        if _named_file(meta.get("superseded_by", "")) != name:
            problems.append("%s does not name %s in superseded_by" % (older, name))
    return problems


def test_superseded_docs_say_so():
    assert superseded_problems(DOCS) == []


def test_superseded_docs_check_flags_an_unmarked_predecessor(tmp_path):
    # negative control: a successor whose predecessor is still marked current
    (tmp_path / "a-SPEC-v1.0.md").write_text("---\ntype: spec\nstatus: accepted\n---\n\n# a\n", encoding="utf-8")
    (tmp_path / "a-SPEC-v1.1.md").write_text("---\nstatus: accepted\nsupersedes: a-SPEC-v1.0.md\n---\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("---\nsupersedes: gone.md (on acceptance)\n---\n", encoding="utf-8")
    assert superseded_problems(tmp_path) == [
        "a-SPEC-v1.0.md is superseded by a-SPEC-v1.1.md but its status is 'accepted'",
        "a-SPEC-v1.0.md does not name a-SPEC-v1.1.md in superseded_by",
        "b.md supersedes gone.md, which does not exist",
    ]
