"""What a connector's ``run`` returns: the outcome of one command, read from the run manifest it wrote.

Every connector writes ``runs/<batch_id>.json`` under its data directory for a run that got as far as normalising.
``result_of_run`` takes the manifest that appeared since a snapshot made before the command (``run_manifests``), so a
run that wrote nothing never reports the manifest of an earlier one.
"""

import contextlib
import contextvars
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Dict, FrozenSet, Iterable, Iterator, List, Optional, Tuple, Type, Union

from meterline.http import failure_kind_of_status

try:
    import fcntl
except ImportError:  # no flock on this platform (Windows): the lock is skipped, with one warning
    fcntl = None  # type: ignore[assignment]

EXIT_OK = 0
EXIT_TRANSIENT = 1  # a 5xx, a 429 after the retries, a timeout or a connection error: run again later
EXIT_CHECKS = 2  # bad arguments or options, no key, a damaged state file, a record check that failed
EXIT_PERMANENT = 3  # the provider refused (400, 401, 403, 404, 422 ...): fix the key, scope or identifier, do not retry

FAILURE_KINDS = ("none", "transient", "input", "permanent")
_KIND_OF_EXIT = {EXIT_OK: "none", EXIT_TRANSIENT: "transient", EXIT_CHECKS: "input", EXIT_PERMANENT: "permanent"}
_EXIT_OF_KIND = {"transient": EXIT_TRANSIENT, "permanent": EXIT_PERMANENT}

_noted_status: Optional[int] = None
_warned_no_fcntl = False
LOCK_NAME = ".lock"


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


# ---- one run per data folder ----------------------------------------------------------------------------------------


class LockUnavailable(RuntimeError):
    """A lock that was required could not be made: no ``fcntl`` on this platform, or the lock file cannot be created."""


def lock_problem(path: Optional[Union[str, Path]] = None) -> Optional[str]:
    """Why ``file_lock(path, required=True)`` would raise, in plain words, or ``None`` when it would not.

    Without ``path`` only the platform is asked. With one, the folder of the lock file is made if needed and the file is
    opened (not locked, and left in place if it was made), as ``file_lock`` does."""
    if fcntl is None:
        return ("file locking (fcntl) is not available on this platform; the job needs it, so it runs on a POSIX system such "
                "as Linux or macOS only")
    if path is not None:
        lock_path = Path(path)
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.close(os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600))
        except OSError as exc:
            return "cannot make the lock file %s (%s); nothing was done" % (lock_path, exc.strerror or type(exc).__name__)
    return None


@contextlib.contextmanager
def file_lock(path: Union[str, Path], what: str = "data folder", required: bool = False) -> Iterator[bool]:
    """An exclusive, non-blocking ``flock`` on the file ``path``, held for the length of the ``with`` block.

    Yields ``True`` when the caller holds the lock and ``False`` when another open file holds it: the message
    ``transient: <what> is in use by another run`` then goes to stderr and the caller must stop and write nothing. The
    operating system releases the lock when the process ends, whatever the way. The file is made 0600 in a 0700 folder.
    By default, without ``fcntl`` the lock is skipped (``True``) with one warning per process, and a lock file that cannot
    be made is a warning too: the run goes on without the lock (what a connector does). With ``required=True`` both raise
    ``LockUnavailable`` before the block, with the message of ``lock_problem``: the job's locks fail closed."""
    global _warned_no_fcntl
    if required:
        problem = lock_problem(path)
        if problem:
            raise LockUnavailable(problem)
    if fcntl is None:
        if not _warned_no_fcntl:
            _warned_no_fcntl = True
            print("warning: file locking (fcntl) is not available on this platform; the %s is not locked, so two runs "
                  "at the same time are not prevented." % what, file=sys.stderr)
        yield True
        return
    lock_path = Path(path)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        print("warning: cannot make the lock file %s (%s); running without the lock." % (lock_path.name, exc.strerror or type(exc).__name__),
              file=sys.stderr)
        yield True
        return
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # EWOULDBLOCK: held by another open file
            print("transient: %s is in use by another run; nothing was done. Run the same command again later." % what, file=sys.stderr)
            yield False
            return
        yield True
    finally:
        os.close(handle)  # closing the file releases the lock


def data_folder_lock(data_dir: Union[str, Path], required: bool = False) -> "contextlib.AbstractContextManager[bool]":
    """The lock of one connector data folder: ``file_lock`` on ``<data_dir>/runs/.lock``.

    It sits inside ``runs/`` so a data folder still holds exactly ``raw``, ``records``, ``runs`` and ``state.json``. Every
    connector's ``run`` takes it first (contract rule R17); ``rebuild``, the silver copy and the export scan ignore the file.
    ``required`` is passed to ``file_lock``: the job asks for it, a connector does not."""
    return file_lock(Path(data_dir) / "runs" / LOCK_NAME, "data folder", required=required)


# ---- auto: backfill until one has published, daily afterwards --------------------------------------------------------

AUTO = "auto"
_requested: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("meterline_requested", default=None)


def _published_manifests(data_dir: Union[str, Path]) -> Iterator[Dict[str, Any]]:
    """Every run manifest under ``data_dir/runs`` whose ``status`` is ``published``; one that cannot be read is passed over."""
    runs = Path(data_dir) / "runs"
    for path in sorted(runs.glob("*.json")) if runs.is_dir() else []:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(manifest, dict) and manifest.get("status") == "published":
            yield manifest


def _published_nothing(manifest: Dict[str, Any]) -> bool:
    """True when the manifest's ``published_counts`` is there and adds up to 0: the run published no record of any kind.

    A manifest without the counts says nothing about it, so it is not called empty."""
    counts = manifest.get("published_counts")
    return isinstance(counts, dict) and sum(v for v in counts.values() if isinstance(v, int) and not isinstance(v, bool)) == 0


def auto_command(data_dir: Union[str, Path]) -> str:
    """What ``auto`` runs in ``data_dir``: ``"backfill"`` until a backfill has published, ``"daily"`` afterwards.

    A backfill has published when a file under ``runs/`` says ``"command": "backfill"`` and ``"status": "published"`` and
    its ``published_counts`` do not add up to 0. A first backfill that published no record is not a backfill: the next
    ``auto`` runs ``backfill`` again, so the history is not skipped for good. ``state.json`` does not say it: a backfill
    that stopped half way writes one too, and the next ``auto`` must resume that backfill. A published ``daily`` is not a
    backfill either (contract rule R18)."""
    return "daily" if any(m.get("command") == "backfill" and not _published_nothing(m)
                          for m in _published_manifests(data_dir)) else "backfill"


def resolve_auto(data_dir: Union[str, Path], since: Any) -> Optional[str]:
    """For ``run("auto", ...)``: the command to run in its place, or ``None`` when ``since`` is missing.

    ``None`` comes with a message on stderr and the connector returns exit 2 (input): ``auto`` needs ``since`` in case
    it backfills. On the daily path ``since`` is not used, and the message says so. Call it under the data folder lock."""
    if since is None:
        print("auto needs --since YYYY-MM-DD: it is the first day of the backfill that auto runs until one has published. "
              "Nothing was done.", file=sys.stderr)
        return None
    chosen = auto_command(data_dir)
    if chosen == "daily":
        print("note: auto runs daily, because a backfill has published in this data folder; --since is ignored",
              file=sys.stderr)
    return chosen


def run_auto(data_dir: Union[str, Path], options: Dict[str, Any], run_one: Callable[[str, Dict[str, Any]], int]) -> int:
    """What a connector's ``run`` does for ``auto``, under its data folder lock; returns the exit code.

    ``run_one(command, options)`` runs ``backfill`` or ``daily`` as the connector always does. ``auto`` without ``since``
    is exit 2 before anything runs; on the daily path ``since`` is left out of the options. The run manifest of the
    chosen command records ``"command"`` (what ran) and ``"requested": "auto"``."""
    chosen = resolve_auto(data_dir, options.get("since"))
    if chosen is None:
        return EXIT_CHECKS
    given = {k: v for k, v in options.items() if not (chosen == "daily" and k == "since")}
    with requested(AUTO):
        return run_one(chosen, given)


def last_published_pull(data_dir: Union[str, Path]) -> Optional[datetime]:
    """The ``pulled_at`` of the latest published run under ``data_dir`` (UTC), ``None`` when no run has published."""
    stamps = []
    for manifest in _published_manifests(data_dir):
        try:
            stamps.append(datetime.fromisoformat(str(manifest["pulled_at"]).replace("Z", "+00:00")).astimezone(timezone.utc))
        except (KeyError, ValueError):
            continue
    return max(stamps) if stamps else None


def final_through(data_dir: Union[str, Path], loaded: Iterable[date], is_final: Callable[[date, datetime], bool]) -> Optional[date]:
    """What a connector's ``complete_through`` returns: the last UTC day it holds as final, ``None`` when there is none.

    ``loaded`` are the days its state file lists as loaded and ``is_final(day, pulled_at)`` its own finality rule. The
    answer is the last day of the unbroken run of loaded days that starts at the first one and is final at the pull of
    the latest published run: a day that is loaded but not yet final (the provider may still revise it), a day not
    loaded, or a gap ends it. ``None`` before a backfill has published (``auto_command`` still says ``backfill``), and
    when the first loaded day is not final yet (contract rule R19)."""
    if auto_command(data_dir) == "backfill":
        return None
    pulled_at = last_published_pull(data_dir)
    days = set(loaded)
    if pulled_at is None or not days:
        return None
    day, last = min(days), None
    while day in days and is_final(day, pulled_at):
        last, day = day, day + timedelta(days=1)
    return last


@contextlib.contextmanager
def requested(command: Optional[str]) -> Iterator[None]:
    """Within the block, ``requested_command()`` returns ``command``: a connector wraps the run of the command that
    ``auto`` chose in ``with requested("auto"):`` so the run manifest it writes records ``"requested": "auto"``."""
    token = _requested.set(command)
    try:
        yield
    finally:
        _requested.reset(token)


def requested_command() -> Optional[str]:
    """The command the caller asked for when it differs from the one run (``"auto"``), else ``None``.

    The default of every connector's ``RunManifest.requested``: a manifest made outside ``requested(...)`` (a plain
    ``backfill``, ``daily``, or one read back from a file without the field) holds ``None``."""
    return _requested.get()


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
