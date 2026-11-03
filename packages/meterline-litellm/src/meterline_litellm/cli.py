"""Command line: ``meterline-litellm backfill | daily | rebuild``.

Exit codes: 0 ok, 1 API or transport failure (state kept, a re-run resumes),
2 checks failed, a path not found, or a configuration error.

The key is read from ``LITELLM_API_KEY`` and the proxy address from
``LITELLM_BASE_URL``, in ``run`` and nowhere else (or from the ``credentials`` its caller passes). The key goes to the client
and nowhere further: not to a file, a log line, a manifest or the state file.
Messages carry counts and statuses, never emails.

Layout under the data directory::

    raw/<endpoint>/<window>/<view>/<pulled_at>.json.gz   every response page as received
    runs/<batch_id>.json                                 one manifest per run
    records/<kind>/<batch_id>.jsonl                      published records
    state.json                                           what is loaded, plus a ``pending`` block
"""

import argparse
import logging
import os
import random
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from meterline import RunResult, note_failure, result_of_run, run_manifests
from meterline.run import (
    AUTO,
    EXIT_PERMANENT,
    EXIT_TRANSIENT,
    WritePhase,
    data_folder_lock,
    exit_code_of_status,
    final_through,
    run_auto,
    unknown_fields_of,
    warn_unknown_fields,
)
from meterline.state import STATE_NAME as STATE_NAME
from meterline.state import StateError as StateError
from meterline.state import is_day_list, is_relative_path, read_state, state_damaged_message, write_state
from meterline.writers import write_jsonl, write_parquet

from .client import MAX_RETRY_AFTER_SECONDS, ApiError, LitellmClient, PaginationError
from .fetch import (
    UNASSIGNED_KEYS,
    PathNotFound,
    RawFileExists,
    RunManifest,
    backward_windows,
    batch_id_for,
    entities_of,
    entity_tag,
    fetch_activity,
    fetch_user_list,
    forward_windows,
    window_label,
)
from .normalise import (
    KINDS,
    NormalisedRun,
    drop_unchanged,
    is_final_day,
    load_published,
    normalise_run,
    read_raw,
    run_checks,
)

logger = logging.getLogger("meterline_litellm.cli")

KEY_ENV = "LITELLM_API_KEY"
URL_ENV = "LITELLM_BASE_URL"
# F-25: the evidence level of the shapes, said where a user meets the command (docs/glossary.md)
SHAPES_NOTE = ("Evidence level: built against documented shapes. The LiteLLM proxy response shapes were transcribed "
               "from its public source code, and the connector is not yet verified against a live proxy; "
               "if a live response differs, follow Response shapes in the package README.")
DEFAULT_DATA_DIR = "exports/meterline-litellm"
PRIVATE_UMASK = 0o077  # data holds emails and per-person usage: files 0600, folders 0700
REREAD_DAYS = 2

EXIT_OK = 0
EXIT_API = 1
EXIT_CHECKS = 2


# ---- state ------------------------------------------------------------------


def _empty_state() -> Dict[str, Any]:
    return {"days_loaded": [], "pending": None}


def _check_state(state: Any) -> None:
    """Raise StateError (naming the first thing wrong, never a value) unless
    ``state`` has the shape ``_write_state`` writes."""
    if not isinstance(state, dict):
        raise StateError("it does not hold a JSON object")
    for key in _empty_state():
        if key not in state:
            raise StateError("the key %s is missing" % key)
    if not is_day_list(state["days_loaded"]):
        raise StateError("days_loaded is not a list of YYYY-MM-DD dates")
    pending = state["pending"]
    if pending is None:
        return
    if not isinstance(pending, dict) or not isinstance(pending.get("command"), str):
        raise StateError("pending is not a block with a command")
    steps = pending.get("steps")
    if not isinstance(steps, dict) or not all(isinstance(k, str) and is_relative_path(v) for k, v in steps.items()):
        raise StateError("pending.steps is not a map of step names to relative file paths")


def _read_state(data_dir: Path) -> Optional[Dict[str, Any]]:
    """The state file as a dict, None when there is none. Raises StateError
    when it is there but damaged."""
    return read_state(data_dir, _check_state)


def _state_damaged(data_dir: Path, exc: StateError) -> int:
    print(state_damaged_message(data_dir, exc), file=sys.stderr)
    return EXIT_CHECKS


_write_state = write_state


class _Stepper:
    """Runs the fetch steps of one run. A step that finished in an earlier,
    failed run of the same command is reused, not requested again."""

    def __init__(self, data_dir: Path, state: Dict[str, Any], command: str) -> None:
        self.data_dir = data_dir
        self.state = state
        self.command = command
        self.done: Dict[str, str] = {}
        pending = state.get("pending")
        if isinstance(pending, dict) and pending.get("command") == command:
            self.done = dict(pending.get("steps") or {})
        self.order: List[str] = []

    def has(self, label: str) -> bool:
        return label in self.done

    def path(self, label: str) -> Path:
        self.order.append(label)
        return self.data_dir / self.done[label]

    def record(self, label: str, path: Path) -> Path:
        self.order.append(label)
        self.done[label] = Path(path).relative_to(self.data_dir).as_posix()
        self.state["pending"] = {"command": self.command, "steps": dict(sorted(self.done.items()))}
        _write_state(self.data_dir, self.state)
        return Path(path)

    def raw_files(self) -> List[str]:
        return [self.done[label] for label in self.order]


# ---- collecting ---------------------------------------------------------------------


def _collect(
    client: LitellmClient,
    data_dir: Path,
    stepper: _Stepper,
    pulled_at: datetime,
    windows: Sequence[Tuple[date, date]],
    proxy_name: str,
    end_users: bool,
) -> Dict[str, int]:
    """Fetch everything one run needs. Returns counts of entities left uncalled."""
    skipped = {"unassigned_keys": 0, "ids_with_comma": 0}
    any_user = False

    def step(label: str, **kw: Any) -> Path:
        if stepper.has(label):
            return stepper.path(label)
        pull = fetch_activity(client, data_dir, pulled_at=pulled_at, proxy_name=proxy_name, **kw)
        return stepper.record(label, pull.path)

    for first, last in windows:
        win = window_label(first, last)
        glob = step("global:" + win, customer=False, first=first, last=last, entity=None)
        for uid in entities_of(glob):
            if uid in UNASSIGNED_KEYS:
                skipped["unassigned_keys"] += 1
                continue
            any_user = True
            step("user:%s:%s" % (win, entity_tag(uid)), customer=False, first=first, last=last, entity=uid)
        if end_users:
            eglob = step("end_global:" + win, customer=True, first=first, last=last, entity=None)
            for eid in entities_of(eglob):
                if eid in UNASSIGNED_KEYS:
                    skipped["unassigned_keys"] += 1
                    continue
                if "," in eid:  # the route splits ids on commas, so it cannot be asked for alone
                    skipped["ids_with_comma"] += 1
                    continue
                step("end:%s:%s" % (win, entity_tag(eid)), customer=True, first=first, last=last, entity=eid)
    if any_user:
        label = "user_list:" + pulled_at.date().isoformat()
        if stepper.has(label):
            stepper.path(label)
        else:
            pull = fetch_user_list(client, data_dir, pulled_at, proxy_name)
            stepper.record(label, pull.path)
    return skipped


# ---- publishing -----------------------------------------------------------------------


def _totals_text(run: NormalisedRun) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for day, metrics in sorted(run.global_days.items()):
        for name, value in sorted(metrics.items()):
            out["global.%s.%s" % (day.isoformat(), name)] = format(value, "f")
    return out


def _publish(
    run: NormalisedRun,
    published: Dict[str, Dict[tuple, Any]],
    records_root: Path,
    batch_id: str,
    parquet_for: Callable[[str], bool],
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for kind, cls in KINDS.items():
        changed = drop_unchanged(run.records(kind), published.get(kind, {}))
        counts[kind] = len(changed)
        if not changed:
            continue
        target = records_root / kind / (batch_id + ".jsonl")
        write_jsonl(changed, target)
        if parquet_for(kind):
            write_parquet(changed, target.with_suffix(".parquet"), cls)
    return counts


def _manifest_for(
    command: str, batch_id: str, pulled_at: datetime, run: NormalisedRun, order: Sequence[str],
    raw_rel: Sequence[str], data_dir: Path, skipped: Dict[str, int],
) -> RunManifest:
    pages = sum(len(read_raw(data_dir / rel)["pages"]) for rel in raw_rel)
    return RunManifest(
        command=command, batch_id=batch_id, pulled_at=pulled_at, windows=list(order), pages=pages,
        row_counts=dict(run.row_counts), totals=_totals_text(run),
        unknown_models=list(run.unknown_models), token_anomalies=list(run.token_anomalies),
        modelless_days=list(run.modelless_days), skipped_entities=dict(skipped),
        raw_files=list(raw_rel), unknown_fields=unknown_fields_of(run.unknown_fields),
    )


def _loadable(covered: Sequence[Tuple[date, date]], present: Set[date], pulled_at: datetime) -> Tuple[Set[date], int]:
    """The days a run may record as loaded, and how many of them came from an answer with no rows.

    A day with rows is loaded. A day without rows is loaded only once it is final (two full days after it ended): before
    that the proxy may still fill it in, and the day stays unloaded."""
    loaded: Set[date] = set()
    empty = 0
    for first, last in covered:
        for n in range((last - first).days + 1):
            day = first + timedelta(days=n)
            if day in present or is_final_day(day, pulled_at):
                loaded.add(day)
                empty += day not in present
    return loaded, empty


def _finish(
    command: str, data_dir: Path, state: Dict[str, Any], stepper: _Stepper, pulled_at: datetime,
    covered: Sequence[Tuple[date, date]], skipped: Dict[str, int], parquet: bool,
) -> int:
    batch_id = batch_id_for(pulled_at)
    raw_rel = stepper.raw_files()
    published = load_published(data_dir)
    try:
        run = normalise_run([data_dir / p for p in raw_rel], batch_id, pulled_at, published=published)
    except ValueError as exc:
        # a response that does not hold what the shapes promise: report, publish nothing
        RunManifest(
            command=command, batch_id=batch_id, pulled_at=pulled_at, status="failed",
            raw_files=list(raw_rel), failures=["raw data not usable: " + str(exc)],
        ).write(data_dir)
        state["pending"] = None
        _write_state(data_dir, state)
        print("raw data not usable: nothing published (%s)" % exc, file=sys.stderr)
        return EXIT_CHECKS
    failures = run_checks(run, pulled_at.date())
    manifest = _manifest_for(command, batch_id, pulled_at, run, _windows_of(stepper), raw_rel, data_dir, skipped)

    if failures:
        manifest.status = "failed"
        manifest.failures = list(failures)
        manifest.write(data_dir)
        state["pending"] = None
        _write_state(data_dir, state)
        print("checks failed: nothing published (%d failures)" % len(failures), file=sys.stderr)
        for message in failures:
            print("  - " + message, file=sys.stderr)
        return EXIT_CHECKS

    warn_unknown_fields("meterline-litellm", manifest.unknown_fields)
    with WritePhase(data_dir, batch_id) as phase:
        counts = _publish(run, published, data_dir / "records", batch_id, lambda _kind: parquet)
        newly_loaded, manifest.empty_days = _loadable(covered, set(run.global_days), pulled_at)
        manifest.status = "published"
        manifest.published_counts = counts
        manifest.write(data_dir)

        loaded = {date.fromisoformat(d) for d in state.get("days_loaded") or []}
        loaded |= newly_loaded
        state["days_loaded"] = sorted(d.isoformat() for d in loaded)
        state["pending"] = None
        _write_state(data_dir, state)
    if phase.failed:
        return EXIT_TRANSIENT
    print(
        "ok: %s published %s" % (command, ", ".join("%s=%d" % kv for kv in sorted(counts.items()))),
        file=sys.stdout,
    )
    return EXIT_OK


def _windows_of(stepper: _Stepper) -> List[str]:
    return list(stepper.order)


def _same_second(data_dir: Path, pulled_at: datetime) -> bool:
    """True, with a message, when a finished run already carries this second as its batch id: its manifest,
    records and raw files would be overwritten or mixed with this run's."""
    if (data_dir / "runs" / (batch_id_for(pulled_at) + ".json")).exists():
        print(
            "a run already finished in the same second (batch %s); wait a second and run again. Nothing was "
            "fetched or changed." % batch_id_for(pulled_at),
            file=sys.stderr,
        )
        return True
    return False


def _api_failed(status: int, detail: Optional[str] = None) -> int:
    """Exit 1 (transient) or 3 (permanent) for an API failure that stopped the run; the status goes to the RunResult."""
    code = exit_code_of_status(status)
    note_failure(status)
    if code == EXIT_PERMANENT:
        if detail:
            print(detail, file=sys.stderr)
        print(
            "permanent: the API refused the request (HTTP status %d); fix the key, its scope or the identifier it "
            "names, then run the same command again. Progress is kept." % status,
            file=sys.stderr,
        )
    else:
        print(
            "transient: re-run later. API request failed (HTTP status %d); progress is kept, run the same command "
            "again to resume" % status,
            file=sys.stderr,
        )
    return code


def _failed(exc: Exception) -> int:
    if isinstance(exc, PathNotFound):
        return _api_failed(404, str(exc))
    return _api_failed(exc.status if isinstance(exc, ApiError) else 0)


# ---- commands ---------------------------------------------------------------------------


def _read_now(now: Callable[[], datetime]) -> datetime:
    value = now()
    if value.tzinfo is None:
        raise ValueError("now() must return a timezone-aware datetime")
    # whole seconds: the batch id and the manifest carry no fraction, and a
    # rebuild must reproduce the pulled_at of the published records exactly
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _run(
    command: str, client: LitellmClient, data_dir: Path, windows: Sequence[Tuple[date, date]],
    pulled_at: datetime, proxy_name: str, end_users: bool, parquet: bool,
) -> int:
    try:
        state = _read_state(data_dir) or _empty_state()
    except StateError as exc:
        return _state_damaged(data_dir, exc)
    if _same_second(data_dir, pulled_at):
        return EXIT_CHECKS
    stepper = _Stepper(data_dir, state, command)
    try:
        skipped = _collect(client, data_dir, stepper, pulled_at, windows, proxy_name, end_users)
    except RawFileExists as exc:
        print("%s; nothing was published. Wait a second and run again." % exc, file=sys.stderr)
        return EXIT_CHECKS
    except (ApiError, PathNotFound) as exc:
        _write_state(data_dir, state)
        return _failed(exc)
    except PaginationError as exc:
        # the pages of a query do not add up: nothing from it is used
        RunManifest(
            command=command, batch_id=batch_id_for(pulled_at), pulled_at=pulled_at, status="failed",
            raw_files=stepper.raw_files(), failures=["pagination: " + str(exc)],
        ).write(data_dir)
        state["pending"] = None
        _write_state(data_dir, state)
        print("pagination: nothing published (%s)" % exc, file=sys.stderr)
        return EXIT_CHECKS
    return _finish(command, data_dir, state, stepper, pulled_at, windows, skipped, parquet)


def run_backfill(
    client: LitellmClient, data_dir: Path, since: date, *, proxy_name: str,
    now: Callable[[], datetime], end_users: bool = False, parquet: bool = False,
) -> int:
    pulled_at = _read_now(now)
    yesterday = pulled_at.date() - timedelta(days=1)
    windows = backward_windows(since, yesterday) if since <= yesterday else []
    return _run("backfill", client, Path(data_dir), windows, pulled_at, proxy_name, end_users, parquet)


def run_daily(
    client: LitellmClient, data_dir: Path, *, proxy_name: str,
    now: Callable[[], datetime], end_users: bool = False, parquet: bool = False,
    since: Optional[date] = None,
) -> int:
    data_dir = Path(data_dir)
    pulled_at = _read_now(now)
    today = pulled_at.date()
    yesterday = today - timedelta(days=1)
    try:
        state = _read_state(data_dir)
    except StateError as exc:
        return _state_damaged(data_dir, exc)
    if state is None:
        # without it, daily cannot tell what is loaded and would quietly fetch the month again
        if since is None:
            print(
                "no state file %s: daily cannot tell which days are already loaded. Pass --since YYYY-MM-DD to start "
                "from that day, or run backfill --since YYYY-MM-DD." % (data_dir / STATE_NAME),
                file=sys.stderr,
            )
            return EXIT_CHECKS
        print("note: no state file %s; starting at %s" % (data_dir / STATE_NAME, since.isoformat()), file=sys.stderr)
        start = since
    else:
        if since is not None:
            print("note: the state file exists, so --since is ignored", file=sys.stderr)
        loaded = [date.fromisoformat(d) for d in state.get("days_loaded") or []]
        start = (max(loaded) + timedelta(days=1)) if loaded else today.replace(day=1)
    start = min(start, today - timedelta(days=REREAD_DAYS))
    windows = forward_windows(start, yesterday)
    return _run("daily", client, data_dir, windows, pulled_at, proxy_name, end_users, parquet)


def run_rebuild(data_dir: Path, parquet: bool = False) -> int:
    """Re-create ``records/`` from the raw store and the run manifests. No
    network, no key."""
    data_dir = Path(data_dir)
    runs_dir = data_dir / "runs"
    manifests: List[RunManifest] = []
    if runs_dir.is_dir():
        for path in sorted(runs_dir.glob("*.json")):
            manifest = RunManifest.read(path)
            if manifest.status == "published":
                manifests.append(manifest)
    manifests.sort(key=lambda m: m.batch_id)

    work = data_dir / ".rebuild"
    if work.exists():
        shutil.rmtree(work)
    new_records = work / "records"
    new_records.mkdir(parents=True)
    old_records = data_dir / "records"

    totals: Dict[str, int] = {kind: 0 for kind in KINDS}
    for manifest in manifests:
        paths = [data_dir / rel for rel in manifest.raw_files]
        missing = [p for p in paths if not p.is_file()]
        if missing:
            print("rebuild: %d raw files missing for run %s" % (len(missing), manifest.batch_id), file=sys.stderr)
            shutil.rmtree(work)
            return EXIT_CHECKS
        published = load_published(work)
        try:
            run = normalise_run(paths, manifest.batch_id, manifest.pulled_at, published=published)
        except ValueError as exc:  # ShapeError is a ValueError
            print("rebuild: raw data not usable for run %s (%s)" % (manifest.batch_id, exc), file=sys.stderr)
            shutil.rmtree(work)
            return EXIT_CHECKS
        failures = run_checks(run, manifest.pulled_at.astimezone(timezone.utc).date())
        if failures:
            print(
                "rebuild: checks failed for run %s (%d failures); records left as they were"
                % (manifest.batch_id, len(failures)),
                file=sys.stderr,
            )
            shutil.rmtree(work)
            return EXIT_CHECKS

        def want_parquet(kind: str, batch: str = manifest.batch_id) -> bool:
            return parquet or (old_records / kind / (batch + ".parquet")).exists()

        counts = _publish(run, published, new_records, manifest.batch_id, want_parquet)
        for kind, n in counts.items():
            totals[kind] += n

    retired = data_dir / "records.old"
    if retired.exists():
        shutil.rmtree(retired)
    if old_records.exists():
        os.replace(old_records, retired)
    os.replace(new_records, old_records)
    if retired.exists():
        shutil.rmtree(retired)
    shutil.rmtree(work)
    print(
        "ok: rebuild of %d runs wrote %s" % (len(manifests), ", ".join("%s=%d" % kv for kv in sorted(totals.items()))),
        file=sys.stdout,
    )
    return EXIT_OK



def complete_through(data_dir: Union[str, Path]) -> Optional[date]:
    """The last UTC day this connector has loaded as final in ``data_dir``, ``None`` before a first published backfill.

    Read from the state file's ``days_loaded`` and the connector's own finality rule (``is_final_day``) at the pull of the
    latest published run: the last day of the unbroken run of loaded days from the first one that is final then
    (``meterline.run.final_through``, contract rule R19). No request, no lock; a damaged state file raises ``StateError``."""
    state = _read_state(Path(data_dir))
    if state is None:
        return None
    return final_through(data_dir, (date.fromisoformat(d) for d in state.get("days_loaded") or []), is_final_day)

# ---- entry point --------------------------------------------------------------------------


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a date as YYYY-MM-DD, got %r" % text) from None


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="where raw, runs, records and state live")
    common.add_argument("--parquet", action="store_true", help="also write <batch_id>.parquet (needs the parquet extra)")
    pull = argparse.ArgumentParser(add_help=False)
    pull.add_argument("--proxy-name", required=True, help="a label for this proxy; becomes provider_account_id")
    pull.add_argument("--rate-per-minute", type=int, default=120, help="request budget per minute (default 120)")
    pull.add_argument("--max-retry-after", type=int, default=MAX_RETRY_AFTER_SECONDS,
                      help="longest wait in seconds a Retry-After header can cause (default 300)")
    pull.add_argument("--end-users", action="store_true", help="also write end_user records (extra calls; off by default)")

    parser = argparse.ArgumentParser(
        prog="meterline-litellm",
        description="Read a LiteLLM proxy's admin API and write meterline records. The key is read from %s and "
        "the proxy address from %s." % (KEY_ENV, URL_ENV),
        epilog=SHAPES_NOTE,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    back = sub.add_parser("backfill", parents=[common, pull], help="walk 31-day windows back from yesterday; resumes after a failure")
    back.add_argument("--since", type=_parse_date, required=True, help="first day to load, YYYY-MM-DD")
    daily = sub.add_parser("daily", parents=[common, pull], help="every closed day since the last one loaded, and always the two days before today")
    daily.add_argument("--since", type=_parse_date, default=None, help="first day to load; needed only when there is no state file")
    auto = sub.add_parser("auto", parents=[common, pull], help="backfill until a backfill has published, daily afterwards")
    auto.add_argument("--since", type=_parse_date, required=True, help="first day of the backfill; ignored once one has published")
    sub.add_parser("rebuild", parents=[common], help="re-create records from the raw store; no API calls")
    return parser


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_OPTIONS = {
    "backfill": ("since", "parquet", "proxy_name", "rate_per_minute", "max_retry_after", "end_users"),
    "daily": ("since", "parquet", "proxy_name", "rate_per_minute", "max_retry_after", "end_users"),
    "auto": ("since", "parquet", "proxy_name", "rate_per_minute", "max_retry_after", "end_users"),
    "rebuild": ("parquet",),
}


def _as_date(value: Any, name: str) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value  # type: ignore[return-value]
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a date or a YYYY-MM-DD string, got %r" % (name, value)) from None


def run(
    command: str,
    *,
    data_dir: Any,
    credentials: Any = None,
    transport: Any = None,
    clock: Callable[[], Any] = time.monotonic,
    sleep: Callable[[Any], Any] = time.sleep,
    now: Optional[Callable[[], datetime]] = None,
    jitter: Callable[[], Any] = random.random,
    wall: Callable[[], Any] = time.time,
    **options: Any,
) -> RunResult:
    """Run one command from Python and return its ``RunResult``; a failed run returns its exit code, it does not raise.

    ``command`` is ``"backfill"``, ``"daily"``, ``"auto"`` or ``"rebuild"``; ``auto`` runs ``backfill`` until a backfill
    has published in ``data_dir`` and ``daily`` afterwards (``meterline.run.auto_command``). ``options`` are the
    command-line options under their Python names, with the command-line defaults: ``since`` (a ``date`` or
    ``YYYY-MM-DD``; required for ``backfill``, and for ``auto``, which exits 2 without it), ``proxy_name`` (required for
    ``backfill``, ``daily`` and ``auto``), ``end_users``, ``rate_per_minute``, ``max_retry_after``
    and ``parquet``; ``rebuild`` takes only ``parquet``. ``base_url`` also sets the proxy address and takes precedence
    over ``LITELLM_BASE_URL`` in ``credentials`` or the environment. An unknown option raises ``TypeError``.

    ``credentials`` maps ``LITELLM_API_KEY`` and ``LITELLM_BASE_URL`` to their values, or is a function without
    arguments that returns such a mapping (called once per run). When it is given the process environment is not
    read for those names, and a name left out counts as unset. When it is ``None`` the environment is read, as the
    command line does. ``transport`` is an httpx transport (``FakeAPI(...).transport``); ``None`` uses the network.
    ``clock``, ``sleep``, ``now``, ``jitter`` and ``wall`` are the test hooks of ``main``.

    Everything written under ``data_dir`` is readable by its owner only (files 0600, folders 0700), whatever the
    caller's umask: the process umask is 077 for the duration of the run and is put back afterwards."""
    if command not in _OPTIONS:
        raise ValueError("command must be one of %s, got %r" % (", ".join(_OPTIONS), command))
    allowed = _OPTIONS[command] + (("base_url",) if command != "rebuild" else ())
    unknown = sorted(set(options) - set(allowed))
    if unknown:
        raise TypeError("run(%r) got an unexpected option: %s" % (command, ", ".join(unknown)))
    data_path = Path(data_dir)
    old_umask = os.umask(PRIVATE_UMASK)
    try:
        with data_folder_lock(data_path) as free:
            if not free:
                return RunResult(exit_code=EXIT_TRANSIENT)  # another run holds this data folder (contract rule R17)
            before = run_manifests(data_path)

            def run_one(chosen: str, given: Dict[str, Any]) -> int:
                return _run_command(
                    chosen, data_path, credentials, transport, given,
                    clock=clock, sleep=sleep, now=now, jitter=jitter, wall=wall,
                )

            code = run_auto(data_path, options, run_one) if command == AUTO else run_one(command, options)
            return result_of_run(data_path, code, before)
    finally:
        os.umask(old_umask)


def _run_command(
    command: str, data_dir: Path, credentials: Any, transport: Any, options: Dict[str, Any], *,
    clock: Callable[[], Any], sleep: Callable[[Any], Any], now: Optional[Callable[[], datetime]],
    jitter: Callable[[], Any], wall: Callable[[], Any],
) -> int:
    parquet = bool(options.get("parquet", False))
    if command == "rebuild":
        return run_rebuild(data_dir, parquet=parquet)

    if "proxy_name" not in options:
        raise TypeError("run(%r) needs the option proxy_name" % command)
    since = _as_date(options.get("since"), "since")
    if command == "backfill" and since is None:
        raise TypeError("run('backfill') needs the option since")
    proxy_name = options["proxy_name"]
    end_users = bool(options.get("end_users", False))
    rate_per_minute = options.get("rate_per_minute", 120)
    max_retry_after = options.get("max_retry_after", MAX_RETRY_AFTER_SECONDS)

    # the HTTP libraries log every request URL, and a URL carries a user id
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if rate_per_minute < 1:
        print("--rate-per-minute must be at least 1", file=sys.stderr)
        return EXIT_CHECKS
    if max_retry_after < 0:
        print("--max-retry-after must not be negative", file=sys.stderr)
        return EXIT_CHECKS
    if credentials is None:
        source: Any = os.environ
    else:
        source = credentials() if callable(credentials) else credentials
    key = source.get(KEY_ENV) or ""
    base = options.get("base_url") or source.get(URL_ENV) or ""
    if not key or not base:
        print(
            "%s and %s must be set in the environment (a key of a user with role proxy_admin_viewer)"
            % (KEY_ENV, URL_ENV),
            file=sys.stderr,
        )
        return EXIT_CHECKS
    if not proxy_name.strip():
        print("--proxy-name must not be empty", file=sys.stderr)
        return EXIT_CHECKS

    clock_now = now if now is not None else _utc_now
    client = LitellmClient(
        key, base_url=base, transport=transport, rate_per_minute=rate_per_minute,
        clock=clock, sleep=sleep, jitter=jitter, wall=wall, max_retry_after=max_retry_after,
    )
    with client:
        if command == "backfill":
            return run_backfill(
                client, data_dir, since, proxy_name=proxy_name, now=clock_now,
                end_users=end_users, parquet=parquet,
            )
        return run_daily(
            client, data_dir, proxy_name=proxy_name, now=clock_now,
            end_users=end_users, parquet=parquet, since=since,
        )


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    transport: Any = None,
    clock: Callable[[], Any] = time.monotonic,
    sleep: Callable[[Any], Any] = time.sleep,
    now: Optional[Callable[[], datetime]] = None,
    jitter: Callable[[], Any] = random.random,
    wall: Callable[[], Any] = time.time,
) -> int:
    """Run one command line and return its exit code; ``run`` does the work, with the key and address read from
    the environment."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    options = {name: getattr(args, name) for name in _OPTIONS[args.command] if hasattr(args, name)}
    result = run(
        args.command, data_dir=args.data_dir, credentials=None, transport=transport,
        clock=clock, sleep=sleep, now=now, jitter=jitter, wall=wall, **options,
    )
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
