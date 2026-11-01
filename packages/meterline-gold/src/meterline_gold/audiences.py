"""Which gold files may be served to which reader: ``audiences.json`` and ``servable(path, audience)``.

``audiences.json`` sits beside this module, is shipped with the package and is committed to
``docs/schemas/gold-audiences.json`` with its own schema. It lists one rule per path pattern of a gold folder.
Nothing is added to the gold folder itself. The table "What a build writes" in the package README says the same
in prose; a test holds the two together.

``serve`` is one of ``audience-only`` (only readers of that audience, and for a keyed folder only the owner of the
key), ``central-only`` (only the central team) or ``never`` (no reader: only the people who run the build).
"""

import json
import re
from pathlib import Path

AUDIENCES = ("central", "exec", "manager", "person")
_FILE = Path(__file__).resolve().parent / "audiences.json"


def load_rules():
    """The parsed ``audiences.json``."""
    return json.loads(_FILE.read_text(encoding="utf-8"))


def _regex(pattern):
    """Regular expression of a path pattern: ``*`` is one path segment, ``{name}`` is a named key segment."""
    parts = []
    for piece in re.split(r"(\*|\{[a-z_]+\})", pattern):
        if piece == "*":
            parts.append("[^/]+")
        elif piece.startswith("{"):
            parts.append("(?P<%s>[^/]+)" % piece[1:-1])
        else:
            parts.append(re.escape(piece))
    return re.compile("".join(parts))


def _normal(path):
    """The path without a leading ``./``, or None when it is not a plain relative path inside the folder."""
    path = str(path)
    while path.startswith("./"):
        path = path[2:]
    if not path or path.startswith("/") or "\\" in path or "\0" in path:
        return None
    if any(part in ("", ".", "..") for part in path.split("/")):
        return None
    return path


def servable(path, audience, rules=None):
    """True when a reader of ``audience`` may be served ``path``, a path relative to the gold folder.

    ``audience`` is ``central``, ``exec``, ``manager`` or ``person``. For ``manager`` and ``person`` it may carry the
    reader's own key, ``manager:<manager_key>`` or ``person:<person_key>``; then the folder must be that key's. Without
    a key, any folder of the audience matches and the caller checks whose it is. A path outside every rule, an unsafe
    path (absolute, backslash, ``..``) and a path of a ``never`` rule give False. An unknown audience is a ValueError.
    """
    name, _, key = audience.partition(":")
    if name not in AUDIENCES:
        raise ValueError("unknown audience %r; use one of %s" % (audience, ", ".join(AUDIENCES)))
    if key and name not in ("manager", "person"):
        raise ValueError("audience %r takes no key" % name)
    rel = _normal(path)
    if rel is None:
        return False
    for rule in (rules or load_rules())["rules"]:
        found = _regex(rule["pattern"]).fullmatch(rel)
        if not found:
            continue
        if rule["serve"] == "never":
            return False
        if rule["serve"] == "central-only":
            return name == "central"
        if name != rule["audience"]:
            return False
        return not key or found.groupdict().get(name + "_key") == key
    return False
