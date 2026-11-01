"""What a connector's ``run`` returns: the outcome of one command, read from the run manifest it wrote.

Every connector writes ``runs/<batch_id>.json`` under its data directory for a run that got as far as normalising.
``result_of_run`` takes the manifest that appeared since a snapshot made before the command (``run_manifests``), so a
run that wrote nothing never reports the manifest of an earlier one.
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Dict, FrozenSet, List, Optional, Tuple, Type, Union

from meterline.http import failure_kind_of_status

EXIT_OK = 0
EXIT_TRANSIENT = 1  # a 5xx, a 429 after the retries, a timeout or a connection error: run again later
EXIT_CHECKS = 2  # bad arguments or options, no key, a damaged state file, a record check that failed
EXIT_PERMANENT = 3  # the provider refused (400, 401, 403, 404, 422 ...): fix the key, scope or identifier, do not retry

FAILURE_KINDS = ("none", "transient", "input", "permanent")
_KIND_OF_EXIT = {EXIT_OK: "none", EXIT_TRANSIENT: "transient", EXIT_CHECKS: "input", EXIT_PERMANENT: "permanent"}
_EXIT_OF_KIND = {"transient": EXIT_TRANSIENT, "permanent": EXIT_PERMANENT}

_noted_status: Optional[int] = None


def exit_code_of_status(status: int) -> int:
    """The exit code of a run that stopped on an HTTP ``status`` (0 for a transport failure): 1 or 3."""
    return _EXIT_OF_KIND[failure_kind_of_status(status)]


def note_failure(http_status: int) -> None:
    """Called by a connector when a run stops on an API failure, so its ``RunResult`` can name the status.

    An API failure writes no run manifest (progress is kept in the state file), so this is the only way the status
    travels; ``result_of_run`` takes it once."""
    global _noted_status
    _noted_status = http_status


@dataclass(frozen=True)
class RunResult:
    """The outcome of one connector run.

    ``exit_code`` is what the command line would exit with: 0 ok, 1 transient (re-run later), 2 input or checks (fix
    the arguments, the state file or the data), 3 permanent provider refusal (fix the key, scope or identifier; do
    not retry). ``failure_kind`` is the same fact by name (``none``, ``transient``, ``input`` or ``permanent``) and
    ``http_status`` the HTTP status that stopped the run, when one did. ``batch_id`` and ``manifest_path`` name the run
    manifest, ``None`` when the run wrote none.
    ``records`` is the count of records published per kind, ``windows`` the windows the run read and ``skipped`` the
    windows it skipped. ``unknown_fields`` lists the fields the provider sent that the connector does not know, as
    ``{"endpoint", "path", "count"}`` items (keys and counts, never a value); it is empty when there were none. The
    run published as usual: an unknown field does not change the exit code.
    """

    exit_code: int
    batch_id: Optional[str] = None
    manifest_path: Optional[Path] = None
    records: Dict[str, int] = field(default_factory=dict)
    windows: Tuple[str, ...] = ()
    skipped: Tuple[str, ...] = ()
    http_status: Optional[int] = None
    unknown_fields: Tuple[Dict[str, Any], ...] = ()

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def failure_kind(self) -> str:
        """``none`` | ``transient`` | ``input`` | ``permanent``, from ``exit_code``; any other code counts as ``input``."""
        return _KIND_OF_EXIT.get(self.exit_code, "input")


def run_manifests(data_dir: Union[str, Path]) -> FrozenSet[str]:
    """The names of the run manifests under ``data_dir`` now; pass the result to ``result_of_run`` after the run."""
    runs = Path(data_dir) / "runs"
    return frozenset(p.name for p in runs.glob("*.json")) if runs.is_dir() else frozenset()


def result_of_run(data_dir: Union[str, Path], exit_code: int, before: FrozenSet[str]) -> RunResult:
    """The ``RunResult`` of a run that exited with ``exit_code``; ``before`` is ``run_manifests`` taken before it."""
    global _noted_status
    status, _noted_status = _noted_status, None
    new = sorted(run_manifests(data_dir) - before)
    if not new:
        return RunResult(exit_code=exit_code, http_status=status)
    path = Path(data_dir) / "runs" / new[-1]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return RunResult(
        exit_code=exit_code,
        batch_id=manifest.get("batch_id"),
        manifest_path=path,
        records=dict(manifest.get("published_counts") or {}),
        windows=tuple(manifest.get("windows") or ()),
        skipped=tuple(manifest.get("skipped_windows") or manifest.get("skipped") or ()),
        http_status=manifest.get("http_status", status),
        unknown_fields=tuple(manifest.get("unknown_fields") or ()),
    )


# ---- fields a normaliser does not know -------------------------------------------------------------------------------

UnknownFields = Dict[Tuple[str, str], int]  # (endpoint, key path) -> how many times the key was met
_LISTED_IN_WARNING = 10


def _join(path: str, name: str) -> str:
    return path + "." + name if path else name


def note_unknown_fields(found: UnknownFields, endpoint: str, node: Any, known: Any, path: str = "") -> None:
    """Add to ``found`` each key of ``node`` that the description ``known`` does not list.

    A connector describes the shape it expects once, as nested data: a dict maps each known key to the description of
    its value, a one-item list describes the items of a list, and ``None`` stands for a value that is not looked into
    (a scalar, or a part of the answer that stays in ``raw/`` and is not mapped). The key ``"*"`` in a dict describes
    every key not listed, for a map whose keys are data (a model, a tool, an id). A value that does not have the
    described type is left to the connector's own required-field checks.

    The result names keys and counts only. A key path is made of key names with ``[]`` for a list and ``*`` for a
    data key, as in ``data[].usage.new_count``, so no value and no name that is data reaches a manifest or a message.
    A key that is not known is counted and not looked into."""
    if isinstance(known, dict):
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            name = str(key)
            if name in known:
                note_unknown_fields(found, endpoint, value, known[name], _join(path, name))
            elif "*" in known:
                note_unknown_fields(found, endpoint, value, known["*"], _join(path, "*"))
            else:
                found[(endpoint, _join(path, name))] = found.get((endpoint, _join(path, name)), 0) + 1
    elif isinstance(known, list) and known and isinstance(node, list):
        for item in node:
            note_unknown_fields(found, endpoint, item, known[0], path + "[]")


def unknown_fields_of(found: UnknownFields) -> List[Dict[str, Any]]:
    """The manifest form of ``found``: ``{"endpoint", "path", "count"}`` items, sorted by endpoint and path."""
    return [{"endpoint": endpoint, "path": path, "count": count} for (endpoint, path), count in sorted(found.items())]


def warn_unknown_fields(connector: str, entries: List[Dict[str, Any]]) -> None:
    """Print one warning line on stderr for a run that met fields its connector does not know; nothing when none.

    The run publishes as usual, so this is a warning and not an error. The line lists the first ten key paths with
    their counts and says how many more the manifest holds."""
    if not entries:
        return
    shown = ", ".join("%s %s (%d)" % (e["endpoint"], e["path"], e["count"]) for e in entries[:_LISTED_IN_WARNING])
    more = len(entries) - _LISTED_IN_WARNING
    print("warning: %s met %d field(s) of the provider that it does not know and left unread: %s%s. The run published as "
          "usual and the fields stay in raw/; the run manifest lists them under unknown_fields. A newer version of the "
          "connector may read them." % (connector, len(entries), shown, "; %d more" % more if more > 0 else ""),
          file=sys.stderr)


class WritePhase:
    """The write phase of a run, as a context manager: all of it lands or none of it does.

    A connector wraps the writes that publish a batch (the record files, the run manifest, the state file) in
    ``with WritePhase(data_dir, batch_id) as phase:``. An ``OSError`` inside (disk full, permission) is not raised: the
    files this batch wrote are removed, a plain message goes to stderr, and ``phase.failed`` is true, so the connector
    returns ``EXIT_TRANSIENT``. The state file is rewritten last and atomically, so it still says what it said before
    the run, and the next run, which reuses the pending raw files, publishes the clean result. Any other exception
    passes through."""

    def __init__(self, data_dir: Union[str, Path], batch_id: str) -> None:
        self.data_dir = Path(data_dir)
        self.batch_id = batch_id
        self.failed = False

    def __enter__(self) -> "WritePhase":
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc: Optional[BaseException], tb: Optional[TracebackType]) -> bool:
        if exc is None or not isinstance(exc, OSError):
            return False
        for path in (*self.data_dir.glob("records/*/%s.*" % self.batch_id), *self.data_dir.glob("runs/%s.json*" % self.batch_id)):
            path.unlink(missing_ok=True)
        self.failed = True
        reason = exc.strerror or type(exc).__name__
        print("transient: writing the results failed (%s); nothing was published, the files of this run were removed and the "
              "state file is unchanged. Run the same command again." % reason, file=sys.stderr)
        return True
