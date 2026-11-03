"""Command line: ``meterline-copilot backfill | daily | rebuild``.

Exit codes: 0 ok, 1 API or transport failure (state kept, a re-run resumes),
2 checks failed, a response that breaks the documented shape, or a
configuration error.

The tokens are read from ``COPILOT_TOKEN`` (usage reports and seats) and
``COPILOT_BILLING_TOKEN`` (billing; defaults to ``COPILOT_TOKEN``), in ``run``
and nowhere else (or taken from the ``credentials`` a caller of ``run`` passes). They go to the
client and nowhere further: not to a file, a log line, a manifest or the state file. Messages carry counts and statuses,
never emails or logins.

Layout under the data directory::

    raw/<endpoint>/<label>/<stamp>.json.gz   every response as received
    runs/<batch_id>.json                     one manifest per run
    records/<kind>/<batch_id>.jsonl          published records
    state.json                               what is loaded, plus a ``pending`` block
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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

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

from .client import MAX_RETRY_AFTER_SECONDS, ApiError, CopilotClient, UnusableBody
from .fetch import (
    EARLIEST_REPORT_DAY,
    BadShape,
    Level,
    RawFileExists,
    RunManifest,
    batch_id_for,
    fetch_billing,
    fetch_report,
    fetch_seat_summary,
    fetch_seats,
    login_tag,
)
from .normalise import (
    KINDS,
    NormalisedRun,
    drop_unchanged,
    is_final_day,
    is_final_month,
    load_published,
    month_people,
    normalise_run,
    read_raw,
    run_checks,
)

logger = logging.getLogger("meterline_copilot.cli")

TOKEN_ENV = "COPILOT_TOKEN"  # nosec B105 # noqa: S105 - the name of an environment variable, not a token
BILLING_TOKEN_ENV = "COPILOT_BILLING_TOKEN"  # nosec B105 # noqa: S105 - the name of an environment variable, not a token
# F-25: the evidence level of the shapes, said where a user meets the command (docs/glossary.md)
SHAPES_NOTE = ("Evidence level: built against documented shapes. The GitHub Copilot response shapes were transcribed "
               "from its documentation, and the connector is not yet verified against a live account; "
               "if a live response differs, follow Response shapes in the package README.")
DEFAULT_DATA_DIR = "exports/meterline-copilot"
PRIVATE_UMASK = 0o077  # data holds emails and per-person usage: files 0600, folders 0700
REREAD_DAYS = 4
PREVIOUS_MONTH_UNTIL_DAY = 8
BILLING_MONTHS_BACK = 24
REPORT_DAYS_BACK = 365

EXIT_OK = 0
EXIT_API = 1
EXIT_CHECKS = 2


# ---- state --------------------------------------------------------------------------


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
    if "provider_account_id" in state and not isinstance(state["provider_account_id"], str):
        raise StateError("provider_account_id is not text")
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
    return read_state(data_dir, _check_renamed_state)


def _check_renamed_state(state: Any) -> None:
    # compatibility: a state file written before the MF-13 rename holds the account under its old name
    if isinstance(state, dict) and "provider_account_id" not in state and "org_id" in state:
        state["provider_account_id"] = state.pop("org_id")
    _check_state(state)


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


# ---- planning -------------------------------------------------------------------------


def _months_back(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) - months
    return date(index // 12, index % 12 + 1, 1)


def _days(first: date, last: date) -> List[date]:
    out: List[date] = []
    cursor = first
    while cursor <= last:
        out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _months_of(first: date, last: date) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    cursor = date(first.year, first.month, 1)
    while cursor <= last:
        out.append((cursor.year, cursor.month))
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return out


# ---- collecting --------------------------------------------------------------------------


def _collect(
    client: CopilotClient, data_dir: Path, stepper: _Stepper, level: Level, pulled_at: datetime,
    days: Sequence[date], months: Sequence[Tuple[int, int]],
) -> None:
    """Fetch everything one run needs: the reports and the organization's
    billing per day, the seat list (and summary), then the billing of every
    person seen in the months asked for."""
    billing_floor = _months_back(pulled_at.date(), BILLING_MONTHS_BACK)

    def step(label: str, make: Callable[[], Any]) -> None:
        if stepper.has(label):
            stepper.path(label)
            return
        stepper.record(label, make().path)

    for day in days:
        tag = day.isoformat()
        step("users:" + tag, lambda d=day: fetch_report(client, data_dir, level, "users", d, pulled_at))
        step("aggregate:" + tag, lambda d=day: fetch_report(client, data_dir, level, "aggregate", d, pulled_at))
        if day >= billing_floor:
            step("billing_day:" + tag, lambda d=day: fetch_billing(
                client, data_dir, level, d.year, d.month, day=d.day, pulled_at=pulled_at))

    today = pulled_at.date().isoformat()
    step("seats:" + today, lambda: fetch_seats(client, data_dir, level, pulled_at))
    if level.kind == "organization":
        step("seat_summary:" + today, lambda: fetch_seat_summary(client, data_dir, level, pulled_at))

    for year, month in months:
        if date(year, month, 1) < billing_floor:
            continue
        people = month_people(data_dir, year, month, pulled_at)
        for login in sorted(people, key=lambda s: (s.lower(), s)):
            step("person:%04d-%02d:%s" % (year, month, login_tag(login)),
                 lambda y=year, m=month, who=login: fetch_billing(client, data_dir, level, y, m, user=who, pulled_at=pulled_at))


# ---- publishing -----------------------------------------------------------------------------


def _totals_text(run: NormalisedRun) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for day, value in sorted(run.raw_credits.items()):
        out["ai_credits_used.%s" % day.isoformat()] = format(value, "f")
    for (product, day), value in sorted(run.raw_tokens.items()):
        out["%s.prompt_tokens_sum.%s" % (product, day.isoformat())] = format(value, "f")
    return out


def _publish(
    run: NormalisedRun, published: Dict[str, Dict[tuple, Any]], records_root: Path, batch_id: str,
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


def _failed_manifest(command: str, batch_id: str, pulled_at: datetime, level: Level, raw_rel: Sequence[str],
                     message: str, data_dir: Path, state: Dict[str, Any]) -> int:
    RunManifest(
        command=command, batch_id=batch_id, pulled_at=pulled_at, status="failed", level=level.kind,
        provider_account_id=level.provider_account_id, raw_files=list(raw_rel), failures=[message],
    ).write(data_dir)
    state["pending"] = None
    _write_state(data_dir, state)
    print("%s: nothing published" % message, file=sys.stderr)
    return EXIT_CHECKS


def _finish(
    command: str, data_dir: Path, state: Dict[str, Any], stepper: _Stepper, level: Level, pulled_at: datetime,
    days: Sequence[date], notes: Dict[str, str], parquet: bool,
) -> int:
    batch_id = batch_id_for(pulled_at)
    raw_rel = stepper.raw_files()
    published = load_published(data_dir)
    try:
        run = normalise_run([data_dir / p for p in raw_rel], data_dir, batch_id, pulled_at, published=published)
    except ValueError as exc:
        # a response that does not hold what the shapes promise: report, publish nothing
        return _failed_manifest(command, batch_id, pulled_at, level, raw_rel, "raw data not usable (%s)" % exc,
                                data_dir, state)
    failures = run_checks(run, pulled_at.date())
    pages = 0
    for rel in raw_rel:
        raw = read_raw(data_dir / rel)
        pages += len(raw.get("pages") or raw.get("files") or [0])
    manifest = RunManifest(
        command=command, batch_id=batch_id, pulled_at=pulled_at, level=level.kind, provider_account_id=level.provider_account_id,
        windows=list(stepper.order), pages=pages, row_counts=dict(run.row_counts), totals=_totals_text(run),
        unknown_products=list(run.unknown_products), unknown_models=list(run.unknown_models),
        unknown_units=list(run.unknown_units), feature_sum_mismatch=list(run.feature_sum_mismatch),
        aggregate_missing=list(run.aggregate_missing), seats_without_assignee=run.seats_without_assignee,
        ai_credits_absent=run.ai_credits_absent, unread_token_families=dict(run.unread_token_families), notes=dict(notes),
        raw_files=list(raw_rel), unknown_fields=unknown_fields_of(run.unknown_fields),
    )
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

    warn_unknown_fields("meterline-copilot", manifest.unknown_fields)
    with WritePhase(data_dir, batch_id) as phase:
        counts = _publish(run, published, data_dir / "records", batch_id, lambda _kind: parquet)
        manifest.status = "published"
        manifest.published_counts = counts
        manifest.write(data_dir)

        loaded = {date.fromisoformat(d) for d in state.get("days_loaded") or []}
        loaded.update(days)
        state["days_loaded"] = sorted(d.isoformat() for d in loaded)
        state["pending"] = None
        _write_state(data_dir, state)
    if phase.failed:
        return EXIT_TRANSIENT
    print("ok: %s published %s" % (command, ", ".join("%s=%d" % kv for kv in sorted(counts.items()))), file=sys.stdout)
    return EXIT_OK


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


def _failed(exc: Exception) -> int:
    return _api_failed(exc.status if isinstance(exc, ApiError) else 0)


# ---- commands --------------------------------------------------------------------------------


def _read_now(now: Callable[[], datetime]) -> datetime:
    value = now()
    if value.tzinfo is None:
        raise ValueError("now() must return a timezone-aware datetime")
    # whole seconds: the batch id and the manifest carry no fraction, and a
    # rebuild must reproduce the pulled_at of the published records exactly
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _run(
    command: str, client: CopilotClient, data_dir: Path, level: Level, days: Sequence[date],
    months: Sequence[Tuple[int, int]], pulled_at: datetime, notes: Dict[str, str], parquet: bool,
) -> int:
    try:
        state = _read_state(data_dir) or _empty_state()
    except StateError as exc:
        return _state_damaged(data_dir, exc)
    if state.get("provider_account_id") not in (None, level.provider_account_id):
        print("this data directory belongs to another organization or level; use a --data-dir of its own", file=sys.stderr)
        return EXIT_CHECKS
    state["provider_account_id"] = level.provider_account_id
    if (data_dir / "runs" / (batch_id_for(pulled_at) + ".json")).exists():
        print(
            "a run already finished in the same second (batch %s); wait a second and run again. Nothing was "
            "fetched or changed." % batch_id_for(pulled_at),
            file=sys.stderr,
        )
        return EXIT_CHECKS
    stepper = _Stepper(data_dir, state, command)
    try:
        _collect(client, data_dir, stepper, level, pulled_at, days, months)
    except RawFileExists as exc:
        print("%s; nothing was published. Wait a second and run again." % exc, file=sys.stderr)
        return EXIT_CHECKS
    except (BadShape, UnusableBody, ValueError) as exc:
        return _failed_manifest(command, batch_id_for(pulled_at), pulled_at, level, stepper.raw_files(),
                                "response not in the documented shape (%s)" % exc, data_dir, state)
    except ApiError as exc:
        _write_state(data_dir, state)
        return _failed(exc)
    return _finish(command, data_dir, state, stepper, level, pulled_at, days, notes, parquet)


def run_backfill(
    client: CopilotClient, data_dir: Path, since: date, *, level: Level,
    now: Callable[[], datetime], parquet: bool = False,
) -> int:
    pulled_at = _read_now(now)
    today = pulled_at.date()
    yesterday = today - timedelta(days=1)
    first = max(since, EARLIEST_REPORT_DAY, today - timedelta(days=REPORT_DAYS_BACK))
    notes: Dict[str, str] = {}
    if first != since:
        notes["since_clamped_to"] = first.isoformat()
        print("note: usage reports exist from %s and go back at most one year; starting at %s" % (
            EARLIEST_REPORT_DAY.isoformat(), first.isoformat()), file=sys.stderr)
    days = _days(first, yesterday) if first <= yesterday else []
    months = _months_of(first, yesterday) if days else []
    return _run("backfill", client, Path(data_dir), level, days, months, pulled_at, notes, parquet)


def run_daily(
    client: CopilotClient, data_dir: Path, *, level: Level, now: Callable[[], datetime], parquet: bool = False,
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
    start = max(min(start, today - timedelta(days=REREAD_DAYS)), EARLIEST_REPORT_DAY)
    days = _days(start, yesterday)
    months = [(today.year, today.month)]
    if today.day <= PREVIOUS_MONTH_UNTIL_DAY:
        prev = _months_back(today, 1)
        months.insert(0, (prev.year, prev.month))
    return _run("daily", client, data_dir, level, days, months, pulled_at, {}, parquet)


def run_rebuild(data_dir: Path, parquet: bool = False) -> int:
    """Re-create ``records/`` from the raw store and the run manifests. No
    network, no token."""
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
            run = normalise_run(paths, data_dir, manifest.batch_id, manifest.pulled_at, published=published)
        except ValueError as exc:
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



def is_complete_day(day: date, pulled_at: datetime) -> bool:
    """A day is complete when the usage rule of the day and the billing rule of its month both hold it as final: the billing
    totals (``org_totals``) of a day stay open until the month is final."""
    return is_final_day(day, pulled_at) and is_final_month(day, pulled_at)


def complete_through(data_dir: Union[str, Path]) -> Optional[date]:
    """The last UTC day this connector has loaded as final in ``data_dir``, ``None`` before a first published backfill.

    Read from the state file's ``days_loaded`` and the connector's own finality rule (``is_complete_day``) at the pull of the
    latest published run: the last day of the unbroken run of loaded days from the first one that is final then
    (``meterline.run.final_through``, contract rule R19). No request, no lock; a damaged state file raises ``StateError``."""
    state = _read_state(Path(data_dir))
    if state is None:
        return None
    return final_through(data_dir, (date.fromisoformat(d) for d in state.get("days_loaded") or []), is_complete_day)

# ---- entry point -------------------------------------------------------------------------------


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
    pull.add_argument("--level", required=True, choices=("organization", "enterprise"), help="read an organization or an enterprise")
    pull.add_argument("--slug", required=True, help="the organization login or the enterprise slug; part of provider_account_id")
    pull.add_argument("--rate-per-hour", type=int, default=600, help="request budget per hour (default 600)")
    pull.add_argument("--max-retry-after", type=int, default=MAX_RETRY_AFTER_SECONDS,
                      help="longest wait in seconds a Retry-After header can cause (default 300)")

    parser = argparse.ArgumentParser(
        prog="meterline-copilot",
        description="Read GitHub Copilot usage reports, billing and seats and write meterline records. Tokens are "
        "read from %s and %s (default: the first)." % (TOKEN_ENV, BILLING_TOKEN_ENV),
        epilog=SHAPES_NOTE,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    back = sub.add_parser("backfill", parents=[common, pull], help="read every day back to --since; resumes after a failure")
    back.add_argument("--since", type=_parse_date, required=True,
                      help="first day to load, YYYY-MM-DD (not before 2025-10-10, not more than a year back)")
    daily = sub.add_parser("daily", parents=[common, pull], help="every closed day since the last one loaded, and always the four days before today")
    daily.add_argument("--since", type=_parse_date, default=None, help="first day to load; needed only when there is no state file")
    auto = sub.add_parser("auto", parents=[common, pull], help="backfill until a backfill has published, daily afterwards")
    auto.add_argument("--since", type=_parse_date, required=True, help="first day of the backfill; ignored once one has published")
    sub.add_parser("rebuild", parents=[common], help="re-create records from the raw store; no API calls")
    return parser


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_OPTIONS = {  # the options of each command under their Python names, with the defaults of the command line
    "backfill": {"since": None, "level": None, "slug": None, "rate_per_hour": 600,
                 "max_retry_after": MAX_RETRY_AFTER_SECONDS, "parquet": False},
    "daily": {"since": None, "level": None, "slug": None, "rate_per_hour": 600,
              "max_retry_after": MAX_RETRY_AFTER_SECONDS, "parquet": False},
    "auto": {"since": None, "level": None, "slug": None, "rate_per_hour": 600,
             "max_retry_after": MAX_RETRY_AFTER_SECONDS, "parquet": False},
    "rebuild": {"parquet": False},
}
# auto without since is not a TypeError: it is exit 2 (input), as the command line's own check (contract rule R18)
_REQUIRED = {"backfill": ("since", "level", "slug"), "daily": ("level", "slug"), "auto": ("level", "slug"), "rebuild": ()}
Credentials = Union[Mapping[str, str], Callable[[], Mapping[str, str]]]


def _as_date(value: Any) -> Optional[date]:
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("since must be a date or a YYYY-MM-DD string")


def run(
    command: str,
    *,
    data_dir: Union[str, Path],
    credentials: Optional[Credentials] = None,
    transport: Any = None,
    clock: Callable[[], Any] = time.monotonic,
    sleep: Callable[[Any], Any] = time.sleep,
    wall: Callable[[], Any] = time.time,
    now: Optional[Callable[[], datetime]] = None,
    jitter: Callable[[], Any] = random.random,
    **options: Any,
) -> RunResult:
    """Run ``backfill``, ``daily``, ``auto`` or ``rebuild`` as the command line does and return its ``RunResult``.

    ``auto`` runs ``backfill`` until a backfill has published in ``data_dir`` and ``daily`` afterwards
    (``meterline.run.auto_command``); it needs ``since`` (exit 2 without it), which it uses only when it backfills.

    ``options`` are the command's options under their Python names (``since``, ``level``, ``slug``,
    ``rate_per_hour``, ``max_retry_after``, ``parquet``), with the defaults of the command line; an unknown one
    raises ``TypeError``. ``credentials`` is a mapping from ``COPILOT_TOKEN`` (and ``COPILOT_BILLING_TOKEN``) to
    its value, or a function that returns one and is called once; when it is given the environment is not
    read. A failed run does not raise: the result carries the exit code the command line would exit with.
    Everything written under the data directory is readable by its owner only (files 0600, folders 0700),
    whatever the caller's umask: the process umask is 077 for the duration of the command and is put back
    afterwards."""
    if command not in _OPTIONS:
        raise ValueError("command must be one of backfill, daily, auto, rebuild, not %r" % (command,))
    known = _OPTIONS[command]
    unknown = sorted(set(options) - set(known))
    if unknown:
        raise TypeError("run(%r) got an unexpected option: %s" % (command, ", ".join(unknown)))
    missing = [name for name in _REQUIRED[command] if options.get(name) is None]
    if missing:
        raise TypeError("run(%r) needs the option: %s" % (command, ", ".join(missing)))
    opts = {**known, **options}
    if "since" in opts:
        opts["since"] = _as_date(opts["since"])
    data_path = Path(data_dir)
    old_umask = os.umask(PRIVATE_UMASK)
    try:
        with data_folder_lock(data_path) as free:
            if not free:
                return RunResult(exit_code=EXIT_TRANSIENT)  # another run holds this data folder (contract rule R17)
            before = run_manifests(data_path)

            def run_one(chosen: str, given: Dict[str, Any]) -> int:
                return _execute(chosen, data_path, {**given, "since": given.get("since")}, credentials, transport, clock, sleep,
                                wall, now, jitter)

            code = run_auto(data_path, opts, run_one) if command == AUTO else run_one(command, opts)
            return result_of_run(data_path, code, before)
    finally:
        os.umask(old_umask)


def _credential_values(credentials: Optional[Credentials]) -> Mapping[str, str]:
    if credentials is None:
        return os.environ
    return credentials() if callable(credentials) else credentials


def _execute(
    command: str, data_dir: Path, opts: Dict[str, Any], credentials: Optional[Credentials], transport: Any,
    clock: Callable[[], Any], sleep: Callable[[Any], Any], wall: Callable[[], Any],
    now: Optional[Callable[[], datetime]], jitter: Callable[[], Any],
) -> int:
    # the HTTP libraries log every request URL, and a URL carries a login
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if command == "rebuild":
        return run_rebuild(data_dir, parquet=opts["parquet"])

    if opts["rate_per_hour"] < 1:
        print("--rate-per-hour must be at least 1", file=sys.stderr)
        return EXIT_CHECKS
    if opts["max_retry_after"] < 0:
        print("--max-retry-after must not be negative", file=sys.stderr)
        return EXIT_CHECKS
    values = _credential_values(credentials)
    usage_token = values.get(TOKEN_ENV, "") or ""
    billing_token = values.get(BILLING_TOKEN_ENV, "") or usage_token
    if not usage_token:
        print("%s must be set in the environment (see the README for what each level needs)" % TOKEN_ENV, file=sys.stderr)
        return EXIT_CHECKS
    try:
        level = Level(opts["level"], opts["slug"])
    except ValueError as exc:
        print("--slug: %s" % exc, file=sys.stderr)
        return EXIT_CHECKS

    clock_now = now if now is not None else _utc_now
    client = CopilotClient(
        usage_token, billing_token=billing_token, transport=transport, rate_per_hour=opts["rate_per_hour"],
        clock=clock, sleep=sleep, wall=wall, jitter=jitter, max_retry_after=opts["max_retry_after"],
    )
    with client:
        if command == "backfill":
            return run_backfill(client, data_dir, opts["since"], level=level, now=clock_now, parquet=opts["parquet"])
        return run_daily(client, data_dir, level=level, now=clock_now, parquet=opts["parquet"], since=opts["since"])


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    transport: Any = None,
    clock: Callable[[], Any] = time.monotonic,
    sleep: Callable[[Any], Any] = time.sleep,
    wall: Callable[[], Any] = time.time,
    now: Optional[Callable[[], datetime]] = None,
    jitter: Callable[[], Any] = random.random,
) -> int:
    """Run one command from the command line: ``run`` with the tokens from the environment."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    options = {name: getattr(args, name) for name in _OPTIONS[args.command]}
    result = run(args.command, data_dir=args.data_dir, credentials=None, transport=transport, clock=clock,
                 sleep=sleep, wall=wall, now=now, jitter=jitter, **options)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
