"""B-7 of the v0.3 hardening spec: the example dashboard is served on the local machine unless the adopter says otherwise.

`python3 -m http.server` listens on every interface and asks for no login. The commands in the documents bind 127.0.0.1, and
the gold README says that shared use needs the adopter's own login in front.

Run: python3 -m pytest -q tools
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_public as ep  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def exported_text_files():
    listed = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    return sorted(f for f in listed if f.endswith((".md", ".txt", ".sh", ".yml", ".yaml", ".toml")) and f.split("/")[0] in ep.INCLUDE
                  and f not in ep.EXCLUDE and not f.startswith("docs/dev/") and os.path.isfile(os.path.join(ROOT, f)))


def test_no_exported_file_shows_http_server_without_binding_the_local_address():
    bare = []
    for rel in exported_text_files():
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if "http.server" in line and "--bind 127.0.0.1" not in line:
                    bare.append("%s:%d" % (rel, n))
    assert bare == []


def test_the_gold_readme_says_shared_use_needs_the_adopters_own_login():
    with open(os.path.join(ROOT, "packages", "meterline-gold", "README.md"), encoding="utf-8") as fh:
        text = fh.read()
    section = text.split("## The example dashboard", 1)[1].split("\n## ", 1)[0]
    assert "python3 -m http.server 8000 --bind 127.0.0.1" in section
    assert "your own login" in section and "shared" in section
