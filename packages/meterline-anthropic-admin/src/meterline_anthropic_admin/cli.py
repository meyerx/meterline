"""Command line: ``meterline-anthropic-admin backfill | daily | rebuild``.

Exit codes: 0 ok, 1 API or transport failure (state kept, a re-run resumes), 2 checks failed, a response of a shape
the connector does not know, or a configuration error.

The key is read from ``ANTHROPIC_ADMIN_KEY`` in ``run`` (or comes from its ``credentials`` argument) and nowhere
else; it is never read from a keychain. The key
goes to the client and nowhere further: not to a file, a log line, a manifest or the state file.

Layout under the data directory::

    raw/<endpoint>/<window>/<pulled_at>.json.gz   every response page as received
    runs/<batch_id>.json                          one manifest per run
    records/<kind>/<batch_id>.jsonl               published records
    state.json                                    what is loaded, plus a ``pending`` block
"""

import argparse
import logging
import os
import random
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

from meterline import RunResult, note_failure, result_of_run, run_manifests
from meterline.run import (
    EXIT_PERMANENT,
    EXIT_TRANSIENT,
    WritePhase,
    exit_code_of_status,
    unknown_fields_of,
    warn_unknown_fields,
)
from meterline.state import STATE_NAME as STATE_NAME
from meterline.state import StateError as StateError
from meterline.state import is_day_list, is_relative_path, read_state, state_damaged_message, write_state
from meterline.writers import write_jsonl, write_parquet

from .client import MAX_RETRY_AFTER_SECONDS, AdminClient, ApiError, PaginationError
from .fetch import (
    COST,
    USAGE,
    RawFileExists,
    RunManifest,
    backward_windows,
    batch_id_for,
    fetch_endpoint,
    forward_windows,
    window_label,
)
from .normalise import (
    KINDS,
    NormalisedRun,
    drop_unchanged,
    first_open_day,
    is_final_day,
    load_published,
    normalise_run,
    read_raw,
    run_checks,
)

logger = logging.getLogger("meterline_anthropic_admin.cli")

KEY_ENV = "ANTHROPIC_ADMIN_KEY"
# the evidence level of the shapes, said where a user meets the command (docs/glossary.md)
SHAPES_NOTE = ("Evidence level: verified live, on a small Console organisation (one model, two days of usage); "
               "cache tokens, priority tier and several workspaces not yet seen. If a live response differs, "
               "follow Response shapes in the package README.")
DEFAULT_DATA_DIR = "exports/meterline-anthropic-admin"
PRIVATE_UMASK = 0o077  # files 0600, folders 0700
REREAD_DAYS = 3  # a day is final 48 hours after it ended, so the three days before today are read once more

EXIT_OK = 0
EXIT_API = 1
EXIT_CHECKS = 2


# ---- state ------------------------------------------------------------------------------------------------------


def _empty_state() -> Dict[str, Any]:
    return {"days_loaded": [], "pending": None}


def _check_state(state: Any) -> None:
    """Raise StateError (naming the first thing wrong, never a value) unless ``state`` has the shape ``_write_state`` writes."""
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
    """Runs the fetch steps of one run. A step that finished in an earlier, failed run of the same command is reused,
    not requested again."""

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


# ---- collecting and publishing ---------------------------------------------------------------------------------------


def _collect(client: AdminClient, data_dir: Path, stepper: _Stepper, pulled_at: datetime, windows: Sequence[Tuple[date, date]],
             provider_account_id: str, workspace_id: Optional[str]) -> None:
    """Fetch everything one run needs: both reports for every window."""
    for first, last in windows:
        for endpoint in (USAGE, COST):
            label = "%s:%s" % (endpoint, window_label(first, last))
            if stepper.has(label):
                stepper.path(label)
                continue
            pull = fetch_endpoint(client, data_dir, endpoint, first=first, last=last, pulled_at=pulled_at, provider_account_id=provider_account_id,
                                  workspace_id=workspace_id)
            stepper.record(label, pull.path)


def _totals_text(run: NormalisedRun) -> Dict[str, str]:
    """Token and cost figures per day and model (or product), as text: what the run report compares with the Console."""
    out: Dict[str, str] = {}
    for rec in run.org_totals:
        who = rec.model or rec.product
        for name in ("tokens_input_uncached", "tokens_cache_read", "tokens_cache_write", "tokens_output", "cost"):
            value = getattr(rec, name)
            if value is not None:
                out["%s.%s.%s" % (rec.period_start.isoformat(), who, name)] = format(value, "f") if isinstance(value, Decimal) else str(value)
    return out


def _publish(run: NormalisedRun, published: Dict[str, Dict[tuple, Any]], records_root: Path, batch_id: str,
             parquet_for: Callable[[str], bool]) -> Dict[str, int]:
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


def _fail_run(command: str, batch_id: str, pulled_at: datetime, raw_rel: Sequence[str], failures: List[str], data_dir: Path,
              state: Dict[str, Any]) -> None:
    RunManifest(command=command, batch_id=batch_id, pulled_at=pulled_at, status="failed", raw_files=list(raw_rel), failures=failures).write(data_dir)
    state["pending"] = None
    _write_state(data_dir, state)


def _loadable(covered: Sequence[Tuple[date, date]], present: Set[date], pulled_at: datetime) -> Tuple[Set[date], int]:
    """The days a run may record as loaded, and how many of them came from an answer with no rows.

    A day with rows is loaded. A day without rows is loaded only once it is final (48 hours after it ended): before that
    the provider may still fill it in, and the day stays unloaded."""
    loaded: Set[date] = set()
    empty = 0
    for first, last in covered:
        for n in range((last - first).days + 1):
            day = first + timedelta(days=n)
            if day in present or is_final_day(day, pulled_at):
                loaded.add(day)
                empty += day not in present
    return loaded, empty


def _finish(command: str, data_dir: Path, state: Dict[str, Any], stepper: _Stepper, pulled_at: datetime,
            covered: Sequence[Tuple[date, date]], parquet: bool) -> int:
    batch_id = batch_id_for(pulled_at)
    raw_rel = stepper.raw_files()
    published = load_published(data_dir)
    try:
        run = normalise_run([data_dir / p for p in raw_rel], batch_id, pulled_at)
    except ValueError as exc:  # ShapeError is a ValueError: a response that is not what the shapes promise
        _fail_run(command, batch_id, pulled_at, raw_rel, ["raw data not usable: " + str(exc)], data_dir, state)
        print("raw data not usable: nothing published (%s)" % exc, file=sys.stderr)
        return EXIT_CHECKS
    failures = run_checks(run, pulled_at.date())
    if failures:
        _fail_run(command, batch_id, pulled_at, raw_rel, failures, data_dir, state)
        print("checks failed: nothing published (%d failures)" % len(failures), file=sys.stderr)
        for message in failures:
            print("  - " + message, file=sys.stderr)
        return EXIT_CHECKS

    unknown = unknown_fields_of(run.unknown_fields)
    warn_unknown_fields("meterline-anthropic-admin", unknown)
    with WritePhase(data_dir, batch_id) as phase:
        counts = _publish(run, published, data_dir / "records", batch_id, lambda _kind: parquet)
        pages = sum(len(read_raw(data_dir / rel)["pages"]) for rel in raw_rel)
        newly_loaded, empty_days = _loadable(covered, run.data_days, pulled_at)
        RunManifest(
            command=command, batch_id=batch_id, pulled_at=pulled_at, status="published", windows=list(stepper.order), pages=pages,
            row_counts=dict(run.row_counts), totals=_totals_text(run), empty_days=empty_days, raw_files=list(raw_rel),
            published_counts=counts, unknown_fields=unknown,
        ).write(data_dir)
        loaded = {date.fromisoformat(d) for d in state.get("days_loaded") or []} | newly_loaded
        state["days_loaded"] = sorted(d.isoformat() for d in loaded)
        state["pending"] = None
        _write_state(data_dir, state)
    if phase.failed:
        return EXIT_TRANSIENT
    print("ok: %s published %s" % (command, ", ".join("%s=%d" % kv for kv in sorted(counts.items()))), file=sys.stdout)
    return EXIT_OK


def _same_second(data_dir: Path, pulled_at: datetime) -> bool:
    """True, with a message, when a finished run already carries this second as its batch id."""
    if (data_dir / "runs" / (batch_id_for(pulled_at) + ".json")).exists():
        print("a run already finished in the same second (batch %s); wait a second and run again. Nothing was fetched or "
              "changed." % batch_id_for(pulled_at), file=sys.stderr)
        return True
    return False


# ---- commands ---------------------------------------------------------------------------------------------------------


def _read_now(now: Callable[[], datetime]) -> datetime:
    value = now()
    if value.tzinfo is None:
        raise ValueError("now() must return a timezone-aware datetime")
    # whole seconds: the batch id and the manifest carry no fraction, and a rebuild must reproduce the pulled_at
    # of the published records exactly
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _run(command: str, client: AdminClient, data_dir: Path, windows: Sequence[Tuple[date, date]], pulled_at: datetime, provider_account_id: str,
         workspace_id: Optional[str], parquet: bool) -> int:
    try:
        state = _read_state(data_dir) or _empty_state()
    except StateError as exc:
        return _state_damaged(data_dir, exc)
    if _same_second(data_dir, pulled_at):
        return EXIT_CHECKS
    stepper = _Stepper(data_dir, state, command)
    try:
        _collect(client, data_dir, stepper, pulled_at, windows, provider_account_id, workspace_id)
    except RawFileExists as exc:
        print("%s; nothing was published. Wait a second and run again." % exc, file=sys.stderr)
        return EXIT_CHECKS
    except ApiError as exc:
        _write_state(data_dir, state)
        return _api_failed(exc.status)
    except PaginationError as exc:  # the pages of a query do not add up: nothing from it is used
        _fail_run(command, batch_id_for(pulled_at), pulled_at, stepper.raw_files(), ["pagination: " + str(exc)], data_dir, state)
        print("pagination: nothing published (%s)" % exc, file=sys.stderr)
        return EXIT_CHECKS
    return _finish(command, data_dir, state, stepper, pulled_at, windows, parquet)


def _api_failed(status: int) -> int:
    """Exit 1 (transient) or 3 (permanent) for an API failure that stopped the run; the status goes to the RunResult."""
    code = exit_code_of_status(status)
    note_failure(status)
    if code == EXIT_PERMANENT:
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


def run_backfill(client: AdminClient, data_dir: Path, since: date, *, provider_account_id: str, now: Callable[[], datetime],
                 workspace_id: Optional[str] = None, parquet: bool = False) -> int:
    pulled_at = _read_now(now)
    yesterday = pulled_at.date() - timedelta(days=1)
    windows = backward_windows(since, yesterday) if since <= yesterday else []
    return _run("backfill", client, Path(data_dir), windows, pulled_at, provider_account_id, workspace_id, parquet)


def _daily_start(data_dir: Path, state: Optional[Dict[str, Any]], since: Optional[date], today: date) -> Optional[date]:
    """The first day ``daily`` reads, or None (with a message) when it cannot tell."""
    if state is None:
        # without it, daily cannot tell what is loaded and would quietly fetch a long stretch again
        if since is None:
            print("no state file %s: daily cannot tell which days are already loaded. Pass --since YYYY-MM-DD to start from that "
                  "day, or run backfill --since YYYY-MM-DD." % (data_dir / STATE_NAME), file=sys.stderr)
            return None
        print("note: no state file %s; starting at %s" % (data_dir / STATE_NAME, since.isoformat()), file=sys.stderr)
        return since
    if since is not None:
        print("note: the state file exists, so --since is ignored", file=sys.stderr)
    loaded = [date.fromisoformat(d) for d in state.get("days_loaded") or []]
    start = (max(loaded) + timedelta(days=1)) if loaded else today - timedelta(days=REREAD_DAYS)
    open_day = first_open_day(load_published(data_dir))  # a day published before it was final is read until it is
    return min(start, today - timedelta(days=REREAD_DAYS), open_day or start)


def run_daily(client: AdminClient, data_dir: Path, *, provider_account_id: str, now: Callable[[], datetime], workspace_id: Optional[str] = None,
              parquet: bool = False, since: Optional[date] = None) -> int:
    data_dir = Path(data_dir)
    pulled_at = _read_now(now)
    today = pulled_at.date()
    try:
        state = _read_state(data_dir)
    except StateError as exc:
        return _state_damaged(data_dir, exc)
    start = _daily_start(data_dir, state, since, today)
    if start is None:
        return EXIT_CHECKS
    windows = forward_windows(start, today - timedelta(days=1))
    return _run("daily", client, data_dir, windows, pulled_at, provider_account_id, workspace_id, parquet)


def run_rebuild(data_dir: Path, parquet: bool = False) -> int:
    """Re-create ``records/`` from the raw store and the run manifests. No network, no key."""
    data_dir = Path(data_dir)
    runs_dir = data_dir / "runs"
    manifests: List[RunManifest] = []
    if runs_dir.is_dir():
        manifests = [m for m in (RunManifest.read(p) for p in sorted(runs_dir.glob("*.json"))) if m.status == "published"]
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
            run = normalise_run(paths, manifest.batch_id, manifest.pulled_at)
        except ValueError as exc:  # ShapeError is a ValueError
            print("rebuild: raw data not usable for run %s (%s)" % (manifest.batch_id, exc), file=sys.stderr)
            shutil.rmtree(work)
            return EXIT_CHECKS
        failures = run_checks(run, manifest.pulled_at.astimezone(timezone.utc).date())
        if failures:
            print("rebuild: checks failed for run %s (%d failures); records left as they were" % (manifest.batch_id, len(failures)),
                  file=sys.stderr)
            shutil.rmtree(work)
            return EXIT_CHECKS

        def want_parquet(kind: str, batch: str = manifest.batch_id) -> bool:
            return parquet or (old_records / kind / (batch + ".parquet")).exists()

        for kind, n in _publish(run, published, new_records, manifest.batch_id, want_parquet).items():
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
    print("ok: rebuild of %d runs wrote %s" % (len(manifests), ", ".join("%s=%d" % kv for kv in sorted(totals.items()))), file=sys.stdout)
    return EXIT_OK


# ---- entry point -----------------------------------------------------------------------------------------------------


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
    pull.add_argument("--provider-account-id", required=True,
                      help="a label for this organisation; becomes provider_account_id (not the Anthropic organisation id)")
    pull.add_argument("--workspace-id", default=None, help="only this workspace (optional; the default is every workspace)")
    pull.add_argument("--rate-per-minute", type=int, default=20, help="request budget per minute (default 20)")
    pull.add_argument("--max-retry-after", type=int, default=MAX_RETRY_AFTER_SECONDS,
                      help="longest wait in seconds a Retry-After header can cause (default 300)")

    parser = argparse.ArgumentParser(
        prog="meterline-anthropic-admin",
        description="Read the Anthropic Admin Usage and Cost API of a Console organisation and write meterline records. "
        "The Admin key is read from %s." % KEY_ENV,
        epilog=SHAPES_NOTE,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    back = sub.add_parser("backfill", parents=[common, pull], help="walk 31-day windows back from yesterday; resumes after a failure")
    back.add_argument("--since", type=_parse_date, required=True, help="first day to load, YYYY-MM-DD")
    daily = sub.add_parser("daily", parents=[common, pull], help="every closed day since the last one loaded, and every day not yet final")
    daily.add_argument("--since", type=_parse_date, default=None, help="first day to load; needed only when there is no state file")
    sub.add_parser("rebuild", parents=[common], help="re-create records from the raw store; no API calls")
    return parser


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


Credentials = Union[Mapping[str, str], Callable[[], Mapping[str, str]], None]

_PULL_OPTIONS = ("provider_account_id", "workspace_id", "rate_per_minute", "max_retry_after", "parquet")
_COMMAND_OPTIONS = {
    "backfill": _PULL_OPTIONS + ("since",),
    "daily": _PULL_OPTIONS + ("since",),
    "rebuild": ("parquet",),
}


def _date_option(name: str, value: Any) -> Optional[date]:
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("%s must be a date or a string YYYY-MM-DD" % name)


def run(
    command: str,
    *,
    data_dir: Union[str, Path],
    credentials: Credentials = None,
    transport: Any = None,
    clock: Callable[[], Any] = time.monotonic,
    sleep: Callable[[Any], Any] = time.sleep,
    now: Optional[Callable[[], datetime]] = None,
    jitter: Callable[[], Any] = random.random,
    wall: Callable[[], Any] = time.time,
    **options: Any,
) -> RunResult:
    """Run one command (``backfill``, ``daily`` or ``rebuild``) and return its ``RunResult``.

    ``options`` are the command line options under their Python names (``since``, ``provider_account_id``, ``workspace_id``,
    ``rate_per_minute``, ``max_retry_after``, ``parquet``), with the same defaults; an unknown name raises
    ``TypeError``. ``credentials`` is a mapping ``{"ANTHROPIC_ADMIN_KEY": key}`` or a function returning one: when it
    is given the environment is not read for the key, and a name missing from it counts as unset. A failed run does
    not raise: ``RunResult.exit_code`` is the code the command line exits with, after the same messages on stderr.
    Everything written under the data directory is readable by its owner only (files 0600, folders 0700), whatever
    the caller's umask: the process umask is 077 for the duration of the command and is put back afterwards."""
    if command not in _COMMAND_OPTIONS:
        raise ValueError("command must be one of backfill, daily, rebuild, not %r" % (command,))
    unknown = sorted(set(options) - set(_COMMAND_OPTIONS[command]))
    if unknown:
        raise TypeError("run(%r) got unexpected option(s): %s" % (command, ", ".join(unknown)))
    data_path = Path(data_dir)
    secrets = credentials() if callable(credentials) else credentials
    before = run_manifests(data_path)
    old_umask = os.umask(PRIVATE_UMASK)
    try:
        code = _run_command(command, data_path, secrets, credentials is not None, options, transport=transport, clock=clock,
                            sleep=sleep, now=now, jitter=jitter, wall=wall)
    finally:
        os.umask(old_umask)
    return result_of_run(data_path, code, before)


def _run_command(command: str, data_dir: Path, secrets: Optional[Mapping[str, str]], given: bool, options: Dict[str, Any], *,
                 transport: Any, clock: Callable[[], Any], sleep: Callable[[Any], Any], now: Optional[Callable[[], datetime]],
                 jitter: Callable[[], Any], wall: Callable[[], Any]) -> int:
    parquet = bool(options.get("parquet", False))
    if command == "rebuild":
        return run_rebuild(data_dir, parquet=parquet)

    since = _date_option("since", options.get("since"))
    provider_account_id = options.get("provider_account_id")
    if provider_account_id is None:
        raise TypeError("run(%r) needs the option provider_account_id" % command)
    if command == "backfill" and since is None:
        raise TypeError("run('backfill') needs the option since")
    rate_per_minute = options.get("rate_per_minute", 20)
    max_retry_after = options.get("max_retry_after", MAX_RETRY_AFTER_SECONDS)
    if rate_per_minute < 1:
        print("--rate-per-minute must be at least 1", file=sys.stderr)
        return EXIT_CHECKS
    if max_retry_after < 0:
        print("--max-retry-after must not be negative", file=sys.stderr)
        return EXIT_CHECKS
    key = (secrets or {}).get(KEY_ENV, "") if given else os.environ.get(KEY_ENV, "")
    if not key:
        print("%s must be set in the environment (an Admin key, sk-ant-admin..., of the organisation)" % KEY_ENV, file=sys.stderr)
        return EXIT_CHECKS
    if not provider_account_id.strip():
        print("--provider-account-id must not be empty", file=sys.stderr)
        return EXIT_CHECKS

    workspace_id = options.get("workspace_id")
    clock_now = now if now is not None else _utc_now
    client = AdminClient(
        key, transport=transport, rate_per_minute=rate_per_minute, clock=clock, sleep=sleep, jitter=jitter, wall=wall,
        max_retry_after=max_retry_after,
    )
    with client:
        if command == "backfill":
            return run_backfill(client, data_dir, since, provider_account_id=provider_account_id, now=clock_now,
                                workspace_id=workspace_id, parquet=parquet)
        return run_daily(client, data_dir, provider_account_id=provider_account_id, now=clock_now, workspace_id=workspace_id,
                         parquet=parquet, since=since)


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
    """Run one command from the command line and return its exit code; the work is ``run``. The key is read from the
    environment."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("httpx", "httpcore"):  # the HTTP libraries log every request URL
        logging.getLogger(noisy).setLevel(logging.WARNING)
    options: Dict[str, Any] = {"parquet": args.parquet}
    if args.command != "rebuild":
        options.update(provider_account_id=args.provider_account_id, workspace_id=args.workspace_id, rate_per_minute=args.rate_per_minute,
                       max_retry_after=args.max_retry_after, since=args.since)
    result = run(args.command, data_dir=args.data_dir, credentials=None, transport=transport, clock=clock, sleep=sleep, now=now,
                 jitter=jitter, wall=wall, **options)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
