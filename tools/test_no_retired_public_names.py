"""MF-13: the retired public names do not appear where a reader or a program meets them.

Retired: `org_id` (now `provider_account_id`), `group_id` (now `cell_kind`), the connector flag `--org-id` (now
`--provider-account-id`) and the gold flag `--org` (now `--org-files`). The scan covers three places: every public
schema (docs/schemas, and the policy schemas that ship inside a package), the `--help` text of every command line,
and the public documents - the files tools/export_public.py would export, chosen by the selection that
tools/test_counts_match_the_tree.py uses, with CHANGELOG.md, docs/decisions/ and the dated records under docs/dev/
left out because they say what was true when they were written.

One kind of text may name a retired name: a paragraph that starts with "Retired names:" (the note in docs/records.md
and the glossary entry that explain the change), or one line that contains "Retired names:" (the note in the
description of a changed schema). The v1 compatibility code and its fixtures are under packages/*/src
and packages/*/tests, which this scan does not read.

Run: python3 -m pytest -q tools/test_no_retired_public_names.py
"""
import glob
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_public as ep  # noqa: E402
from test_counts_match_the_tree import DATED_DEV_RECORD, LEFT_OUT  # noqa: E402
from test_docs_structure import tracked_files  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RETIRED = re.compile(r"\borg_id\b|\bgroup_id\b|--org-id\b|--org(?![\w-])")
NOTE_MARKER = "Retired names:"
TEXT_SUFFIXES = (".md", ".txt", ".yml", ".yaml", ".toml", ".sql", ".json", ".csv", ".jsonl", ".tmpl")
COMMANDS = [
    ("meterline_anthropic.cli", ["backfill", "daily", "rebuild"]),
    ("meterline_anthropic_admin.cli", ["backfill", "daily", "rebuild"]),
    ("meterline_copilot.cli", ["backfill", "daily", "rebuild"]),
    ("meterline_litellm.cli", ["backfill", "daily", "rebuild"]),
    ("meterline_gold.cli", ["build", "schemas", "import-litellm-prices"]),
    ("meterline_sim.cli", ["generate"]),
]


def retired_in(text):
    """(line number, matched text) for each retired name outside a note.

    A note is a paragraph that starts with "Retired names:", or a single line that contains it (the description string
    of a JSON schema is one line inside a paragraph that is the whole file)."""
    hits, number = [], 0
    for paragraph in re.split(r"(\n[ \t]*\n)", text):
        lines = paragraph.count("\n")
        if not paragraph.lstrip().startswith(NOTE_MARKER):
            for offset, line in enumerate(paragraph.split("\n")):
                if NOTE_MARKER not in line:
                    hits += [(number + offset + 1, m.group(0)) for m in RETIRED.finditer(line)]
        number += lines
    return hits


def public_documents(tmp_path):
    """Relative paths of the exportable text files a reader meets: the export's own selection and docs/dev rule."""
    for rel in tracked_files(ROOT):
        if not any(rel == p or rel.startswith(p + "/") for p in ep.INCLUDE) or rel in ep.EXCLUDE:
            continue
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, rel), dest)
    ep.prune_dev(str(tmp_path))
    kept = []
    for rel in ep._tree_files(str(tmp_path)):
        if rel in LEFT_OUT or rel.startswith(LEFT_OUT[1]) or DATED_DEV_RECORD.match(rel):
            continue
        if rel.startswith("packages/") and not re.fullmatch(r"packages/[^/]+/README\.md", rel):
            continue  # code and tests: v1 compatibility lives there; the schemas and help text are scanned below
        if rel.startswith("tools/") and not rel.startswith("tools/connector_template/"):
            continue
        if rel.endswith(TEXT_SUFFIXES) or (rel.startswith("examples/") and rel.endswith(".py")):
            kept.append(rel)
    return sorted(kept)


def schema_files():
    return sorted(glob.glob(os.path.join(ROOT, "docs", "schemas", "*.json"))
                  + glob.glob(os.path.join(ROOT, "packages", "*", "src", "*", "schemas", "*.json")))


def help_texts():
    out = {}
    for module, subs in COMMANDS:
        code = "import sys; from %s import main; main(sys.argv[1:])" % module
        for args in ([], *[[s] for s in subs]):
            run = subprocess.run([sys.executable, "-c", code, *args, "--help"], capture_output=True, text=True, check=False, cwd=ROOT)
            out["%s %s --help" % (module.split(".")[0], " ".join(args))] = run.stdout + run.stderr
    return out


def test_no_retired_public_names_in_the_schemas():
    files = schema_files()
    assert len(files) >= 40, files
    wrong = []
    for path in files:
        with open(path, encoding="utf-8") as fh:
            wrong += ["%s:%d: %s" % (os.path.relpath(path, ROOT), n, hit) for n, hit in retired_in(fh.read())]
    assert not wrong, "\n" + "\n".join(wrong)


def test_no_retired_public_names_in_the_help_text():
    texts = help_texts()
    assert len(texts) >= 20 and all("usage:" in text for text in texts.values()), sorted(texts)
    wrong = ["%s: %s" % (name, hit) for name, text in texts.items() for _, hit in retired_in(text)]
    assert not wrong, "\n" + "\n".join(wrong)


def test_no_retired_public_names_in_the_public_documents(tmp_path):
    documents = public_documents(tmp_path)
    assert "README.md" in documents and "docs/records.md" in documents and "docs/glossary.md" in documents, documents[:20]
    wrong = []
    for rel in documents:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            wrong += ["%s:%d: %s" % (rel, n, hit) for n, hit in retired_in(fh.read())]
    assert not wrong, "\n" + "\n".join(wrong)


@pytest.mark.parametrize("planted, names", [
    ("a column `org_id` in a table", ["org_id"]),
    ("WHERE group_id = 'unit'", ["group_id"]),
    ("meterline-gold build --silver S --org DIR --out gold", ["--org"]),
    ("meterline-anthropic-admin daily --org-id my-org", ["--org-id"]),
])
def test_the_scan_flags_a_retired_name(planted, names):
    # negative control: the same predicate on planted text
    assert [hit for _, hit in retired_in(planted)] == names


def test_the_scan_passes_the_new_names_and_the_note():
    ok = "provider_account_id, cell_kind, --provider-account-id, --org-files, rbac_group_id, org_identities, an `org:` cell"
    assert retired_in(ok) == []
    note = "Intro line.\n\n%s `org_id` became `provider_account_id`;\nso did `group_id`.\n\nA later `org_id` paragraph." % NOTE_MARKER
    assert retired_in(note) == [(6, "org_id")]
    json_line = '{\n  "description": "A row. %s org_id is now x.",\n  "other": "org_id"\n}' % NOTE_MARKER
    assert retired_in(json_line) == [(3, "org_id")]
