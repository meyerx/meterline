"""Command line: ``meterline-anthropic backfill | daily | rebuild``.

Exit codes: 0 ok, 1 API or transport failure (state kept, a re-run resumes),
2 checks failed or configuration error.

The API key is read from ``ANTHROPIC_ANALYTICS_KEY`` in ``main`` and nowhere
else. It goes to the client and nowhere further: not to a file, a log line, a
manifest or the state file. Messages carry counts and statuses, never emails.

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
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union, cast

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

from .client import MAX_RETRY_AFTER_SECONDS, AnalyticsClient, ApiError
from .fetch import (
    RawFileExists,
    RunManifest,
    batch_id_for,
    fetch_activity_day,
    fetch_org_totals,
    fetch_summaries_windows,
    fetch_usage_window,
    windows as split_windows,
)
from .normalise import (
    KINDS,
    NormalisedRun,
    drop_unchanged,
    is_final_period,
    load_published,
    normalise_run,
    parse_timestamp,
    read_raw,
    row_count,
    run_checks,
)

logger = logging.getLogger("meterline_anthropic.cli")

KEY_ENV = "ANTHROPIC_ANALYTICS_KEY"
# F-25: the evidence level of the shapes, said where a user meets the command (docs/glossary.md)
SHAPES_NOTE = ("Evidence level: built against documented shapes. The Anthropic Analytics API response shapes were transcribed "
               "from its documentation, and the connector is not yet verified against a live organization; "
               "if a live response differs, follow Response shapes in the package README.")
DEFAULT_DATA_DIR = "exports/meterline-anthropic"
PRIVATE_UMASK = 0o077  # data holds emails and per-person usage: files 0600, folders 0700
PREVIOUS_MONTH_UNTIL_DAY = 8
DATA_STARTS = date(2026, 1, 1)  # spec 2: the API holds data from this day
MAX_DAYS_BACK = 365  # spec 2: starting_at at most this many days back

EXIT_OK = 0
EXIT_API = 1
EXIT_CHECKS = 2


# ---- state ------------------------------------------------------------------


def _empty_state() -> Dict[str, Any]:
    return {
        "activity_loaded": [],
        "summaries_loaded": [],
        "refreshed_at": {},
        "latest_activity_date": None,
        "pending": None,
    }


def _is_label_map(value: Any, paths: bool) -> bool:
    if not isinstance(value, dict):
        return False
    for label, items in value.items():
        if not isinstance(label, str) or not isinstance(items, list):
            return False
        if not all(is_relative_path(i) if paths else isinstance(i, str) for i in items):
            return False
    return True


def _check_state(state: Any) -> None:
    """Raise StateError (naming the first thing wrong, never a value) unless
    ``state`` has the shape ``_write_state`` writes."""
    if not isinstance(state, dict):
        raise StateError("it does not hold a JSON object")
    for key in _empty_state():
        if key not in state:
            raise StateError("the key %s is missing" % key)
    for key in ("activity_loaded", "summaries_loaded"):
        if not is_day_list(state[key]):
            raise StateError("%s is not a list of YYYY-MM-DD dates" % key)
    refreshed = state["refreshed_at"]
    if not isinstance(refreshed, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in refreshed.items()):
        raise StateError("refreshed_at is not a map of text to text")
    latest = state["latest_activity_date"]
    if latest is not None and not is_day_list([latest]):
        raise StateError("latest_activity_date is not a YYYY-MM-DD date")
    pending = state["pending"]
    if pending is None:
        return
    if not isinstance(pending, dict) or not isinstance(pending.get("command"), str):
        raise StateError("pending is not a block with a command")
    if not _is_label_map(pending.get("steps"), paths=True):
        raise StateError("pending.steps is not a map of step names to relative file paths")
    if "skipped" in pending and not _is_label_map(pending["skipped"], paths=False):
        raise StateError("pending.skipped is not a map of step names to text lists")


def _read_state(data_dir: Path) -> Optional[Dict[str, Any]]:
    """The state file as a dict, None when there is none. Raises StateError
    when it is there but damaged."""
    return read_state(data_dir, _check_state)


def _state_damaged(data_dir: Path, exc: StateError) -> int:
    print(state_damaged_message(data_dir, exc), file=sys.stderr)
    return EXIT_CHECKS


_write_state = write_state


def _days(values: Sequence[str]) -> set:
    return {date.fromisoformat(v) for v in values}


def _day_list(values: Any) -> List[str]:
    return sorted(d.isoformat() for d in values)


# ---- steps (resume) -----------------------------------------------------------


class _Stepper:
    """Runs the fetch steps of one run. A step that finished in an earlier,
    failed run of the same command is reused, not requested again. After each
    finished step the state file is rewritten with a ``pending`` block."""

    def __init__(self, data_dir: Path, state: Dict[str, Any], command: str) -> None:
        self.data_dir = data_dir
        self.state = state
        self.command = command
        self.done: Dict[str, List[str]] = {}
        self.skipped: Dict[str, List[str]] = {}
        pending = state.get("pending")
        if isinstance(pending, dict) and pending.get("command") == command:
            self.done = {k: list(v) for k, v in (pending.get("steps") or {}).items()}
            self.skipped = {k: list(v) for k, v in (pending.get("skipped") or {}).items()}
        self.order: List[str] = []

    def has(self, label: str) -> bool:
        return label in self.done

    def reuse(self, label: str) -> None:
        self.order.append(label)

    def record(self, label: str, paths: Sequence[Path], skipped: Sequence[str] = ()) -> None:
        self.order.append(label)
        self.done[label] = [Path(p).relative_to(self.data_dir).as_posix() for p in paths]
        if skipped:
            self.skipped[label] = list(skipped)
        self.save()

    def skipped_windows(self) -> List[str]:
        """Windows skipped (\"no data yet\") by the steps of this run, in order."""
        return [s for label in self.order for s in self.skipped.get(label, [])]

    def save(self) -> None:
        names = sorted(self.done)
        self.state["pending"] = {
            "command": self.command,
            "windows_done": names,
            "raw_files": [p for n in names for p in self.done[n]],
            "steps": {n: self.done[n] for n in names},
            "skipped": {n: self.skipped[n] for n in names if n in self.skipped},
        }
        _write_state(self.data_dir, self.state)

    def raw_files(self) -> List[str]:
        return [p for label in self.order for p in self.done[label]]


# ---- plan helpers ---------------------------------------------------------------


def _date_range(first: date, last: date) -> List[date]:
    out: List[date] = []
    cursor = first
    while cursor <= last:
        out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _collect(
    client: AnalyticsClient,
    data_dir: Path,
    state: Dict[str, Any],
    stepper: _Stepper,
    pulled_at: datetime,
    report_windows: Sequence[Tuple[date, date]],
    activity_start: date,
    summaries_start: date,
) -> Tuple[List[date], List[date]]:
    """Fetch everything one run needs. Returns (activity days loaded this run
    including reused ones, summary days covered). Raises ApiError."""
    today = pulled_at.date()
    yesterday = today - timedelta(days=1)
    provider_account_id: Optional[str] = None  # from the first report pull; None: the fetchers learn it

    def remember(paths: Sequence[Path], pulls: Sequence[Any]) -> None:
        nonlocal provider_account_id
        if provider_account_id is None:
            for pull in pulls:
                if pull.provider_account_id:
                    provider_account_id = pull.provider_account_id
                    return
            for path in paths:
                found = read_raw(path).get("organization_id")
                if found:
                    provider_account_id = str(found)
                    return

    for first, last in report_windows:
        label = "%s_%s" % (first.isoformat(), last.isoformat())
        for step, fetcher in (("usage:", fetch_usage_window), ("org:", fetch_org_totals)):
            if stepper.has(step + label):
                stepper.reuse(step + label)
                remember([data_dir / p for p in stepper.done[step + label]], [])
            else:
                skipped: List[str] = []
                pulls = fetcher(client, data_dir, first, last, pulled_at, skipped)
                stepper.record(step + label, [p.path for p in pulls], skipped)
                remember([], pulls)

    already = _days(state.get("activity_loaded") or [])
    loaded_activity: List[date] = []
    unavailable: List[date] = []
    for day in _date_range(activity_start, yesterday):
        if day in already:
            continue
        label = "users:" + day.isoformat()
        if stepper.has(label):
            stepper.reuse(label)
            loaded_activity.append(day)
            continue
        pull = fetch_activity_day(
            client, data_dir, day, pulled_at, on_unavailable=unavailable.append, provider_account_id=provider_account_id
        )
        if pull is None:
            state["latest_activity_date"] = unavailable[-1].isoformat()
            break
        stepper.record(label, [pull.path])
        loaded_activity.append(day)

    have = _days(state.get("summaries_loaded") or [])
    missing = [d for d in _date_range(summaries_start, yesterday) if d not in have]
    if missing:
        first, last = min(missing), max(missing)
        label = "summaries:%s_%s" % (first.isoformat(), last.isoformat())
        if stepper.has(label):
            stepper.reuse(label)
        else:
            not_yet: List[date] = []
            pulls = fetch_summaries_windows(client, data_dir, first, last, pulled_at, provider_account_id=provider_account_id,
                                            on_unavailable=not_yet.append)
            stepper.record(label, [p.path for p in pulls])
            if not_yet:  # the days after the latest available one are asked for again by the next run
                missing = [d for d in missing if d <= max(not_yet)]
    return loaded_activity, missing


# ---- publishing -----------------------------------------------------------------


def _totals_text(run: NormalisedRun) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for kind in ("usage", "org_totals", "activity"):
        for name, value in sorted(run.raw_totals.get(kind, {}).items(), key=lambda kv: str(kv[0])):
            out["%s.%s" % (kind, name)] = format(value, "f")
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
    command: str,
    batch_id: str,
    pulled_at: datetime,
    run: NormalisedRun,
    order: Sequence[str],
    raw_rel: Sequence[str],
    data_dir: Path,
    skipped: Sequence[str] = (),
) -> RunManifest:
    pages = 0
    for rel in raw_rel:
        pages += len(read_raw(data_dir / rel)["pages"])
    return RunManifest(
        command=command,
        batch_id=batch_id,
        pulled_at=pulled_at,
        windows=list(order),
        pages=pages,
        row_counts=dict(run.row_counts),
        totals=_totals_text(run),
        unknown_products=list(run.unknown_products),
        unknown_models=list(run.unknown_models),
        skipped_windows=list(skipped),
        cost_only=list(run.cost_only),
        raw_files=list(raw_rel),
        unknown_fields=unknown_fields_of(run.unknown_fields),
    )


def _settle(days: Sequence[date], present: Set[date], pulled_at: datetime) -> Tuple[List[date], int]:
    """The days a run may record as loaded, and how many of those came from an empty answer.

    A day the answer holds rows for is loaded. An empty day is loaded only once its period is final: before that the
    provider may still fill it in, so it stays unloaded, and so does every day after it, because ``daily`` starts after
    the latest loaded day and must come back to it."""
    loaded: List[date] = []
    empty = 0
    gap = False
    for day in sorted(days):
        if day not in present and not is_final_period(day, pulled_at):
            gap = True
        if gap:
            continue
        loaded.append(day)
        empty += day not in present
    return loaded, empty


def _days_with_rows(data_dir: Path, raw_rel: Sequence[str]) -> Set[date]:
    """The days whose ``users`` answer held at least one row."""
    found: Set[date] = set()
    for rel in raw_rel:
        raw = read_raw(data_dir / rel)
        if raw["endpoint"] == "users" and row_count(raw):
            found.add(date.fromisoformat(raw["window"][:10]))
    return found


def _finish(
    command: str,
    data_dir: Path,
    state: Dict[str, Any],
    stepper: _Stepper,
    pulled_at: datetime,
    loaded_activity: Sequence[date],
    loaded_summaries: Sequence[date],
    parquet: bool,
) -> int:
    batch_id = batch_id_for(pulled_at)
    raw_rel = stepper.raw_files()
    published = load_published(data_dir)
    try:
        run = normalise_run([data_dir / p for p in raw_rel], batch_id, pulled_at, published=published)
    except ValueError as exc:
        return _unusable(command, data_dir, state, pulled_at, raw_rel, exc)
    failures = run_checks(run, pulled_at.date())
    manifest = _manifest_for(
        command, batch_id, pulled_at, run, stepper.order, raw_rel, data_dir, stepper.skipped_windows()
    )
    loaded_activity, empty_activity = _settle(loaded_activity, _days_with_rows(data_dir, raw_rel), pulled_at)
    loaded_summaries, empty_summaries = _settle(loaded_summaries, {r.period_start for r in run.seats}, pulled_at)
    manifest.empty_days = empty_activity + empty_summaries

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

    warn_unknown_fields("meterline-anthropic", manifest.unknown_fields)
    with WritePhase(data_dir, batch_id) as phase:
        counts = _publish(run, published, data_dir / "records", batch_id, lambda _kind: parquet)
        manifest.status = "published"
        manifest.published_counts = counts
        manifest.write(data_dir)

        refreshed: Dict[str, str] = dict(state.get("refreshed_at") or {})
        for rel in raw_rel:
            raw = read_raw(data_dir / rel)
            endpoint, stamp = raw["endpoint"], raw.get("data_refreshed_at")
            if not stamp:
                continue
            if endpoint not in refreshed or parse_timestamp(stamp) > parse_timestamp(refreshed[endpoint]):
                refreshed[endpoint] = stamp
        state["refreshed_at"] = refreshed
        state["activity_loaded"] = _day_list(_days(state.get("activity_loaded") or []) | set(loaded_activity))
        state["summaries_loaded"] = _day_list(_days(state.get("summaries_loaded") or []) | set(loaded_summaries))
        state["pending"] = None
        _write_state(data_dir, state)
    if phase.failed:
        return EXIT_TRANSIENT
    print(
        "ok: %s published %s" % (command, ", ".join("%s=%d" % kv for kv in sorted(counts.items()))),
        file=sys.stdout,
    )
    return EXIT_OK


def _unusable(
    command: str, data_dir: Path, state: Dict[str, Any], pulled_at: datetime, raw_rel: Sequence[str], exc: Exception
) -> int:
    """A response that does not hold what the documented shapes promise (a
    missing or mistyped field): a failed manifest, nothing published, exit 2.
    The message names the endpoint and the field."""
    message = "raw data not usable (%s)" % exc
    RunManifest(
        command=command, batch_id=batch_id_for(pulled_at), pulled_at=pulled_at, status="failed",
        raw_files=list(raw_rel), failures=[message],
    ).write(data_dir)
    state["pending"] = None
    _write_state(data_dir, state)
    print("%s: nothing published" % message, file=sys.stderr)
    return EXIT_CHECKS


def _same_second(data_dir: Path, pulled_at: datetime) -> bool:
    """True, with a message, when a finished run already carries this second as its batch id: its manifest,
    records and raw files would be overwritten or mixed with this run's."""
    if (data_dir / "runs" / (batch_id_for(pulled_at) + ".json")).exists():
        print(
            "a run already finished in the same second (batch %s); wait a second and run again. Nothing was fetched "
            "or changed." % batch_id_for(pulled_at),
            file=sys.stderr,
        )
        return True
    return False


def _raw_exists(exc: RawFileExists) -> int:
    print("%s; nothing was published. Wait a second and run again." % exc, file=sys.stderr)
    return EXIT_CHECKS


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


# ---- commands ---------------------------------------------------------------------


def _since_refused(since: date, today: date) -> bool:
    """True, with a message, when ``since`` is outside what the API holds (spec 2): before 2026-01-01 or more
    than 365 days back (so at most 364 whole days before today)."""
    if since < DATA_STARTS:
        print("--since %s is before %s, where the API's data starts; use a later day." % (since, DATA_STARTS),
              file=sys.stderr)
        return True
    if (today - since).days >= MAX_DAYS_BACK:  # midnight of the start day must be within 365 days of now
        print("--since %s is more than %d days before today; the API takes a start at most %d days back. "
              "Use %s or later." % (since, MAX_DAYS_BACK, MAX_DAYS_BACK, today - timedelta(days=MAX_DAYS_BACK - 1)),
              file=sys.stderr)
        return True
    return False


def _read_now(now: Callable[[], datetime]) -> datetime:
    value = now()
    if value.tzinfo is None:
        raise ValueError("now() must return a timezone-aware datetime")
    # whole seconds: the batch id and the manifest carry no fraction, and a
    # rebuild must reproduce the pulled_at of the published records exactly
    return value.astimezone(timezone.utc).replace(microsecond=0)


def run_backfill(
    client: AnalyticsClient,
    data_dir: Path,
    since: date,
    *,
    now: Callable[[], datetime],
    parquet: bool = False,
) -> int:
    data_dir = Path(data_dir)
    pulled_at = _read_now(now)
    today = pulled_at.date()
    if _since_refused(since, today):
        return EXIT_CHECKS
    try:
        state = _read_state(data_dir) or _empty_state()
    except StateError as exc:
        return _state_damaged(data_dir, exc)
    if _same_second(data_dir, pulled_at):
        return EXIT_CHECKS
    stepper = _Stepper(data_dir, state, "backfill")
    try:
        activity, summaries = _collect(
            client, data_dir, state, stepper, pulled_at,
            split_windows(since, today), since, since,
        )
    except ApiError as exc:
        _write_state(data_dir, state)
        return _api_failed(exc.status)
    except RawFileExists as exc:
        return _raw_exists(exc)
    except ValueError as exc:
        return _unusable("backfill", data_dir, state, pulled_at, stepper.raw_files(), exc)
    return _finish("backfill", data_dir, state, stepper, pulled_at, activity, summaries, parquet)


def run_daily(
    client: AnalyticsClient,
    data_dir: Path,
    *,
    now: Callable[[], datetime],
    parquet: bool = False,
    since: Optional[date] = None,
) -> int:
    data_dir = Path(data_dir)
    pulled_at = _read_now(now)
    today = pulled_at.date()
    if since is not None and _since_refused(since, today):
        return EXIT_CHECKS
    month_first = today.replace(day=1)
    report_windows: List[Tuple[date, date]] = []
    if today.day <= PREVIOUS_MONTH_UNTIL_DAY:
        prev_last = month_first - timedelta(days=1)
        report_windows.append((prev_last.replace(day=1), prev_last))
    report_windows.append((month_first, today))

    try:
        found = _read_state(data_dir)
    except StateError as exc:
        return _state_damaged(data_dir, exc)
    if found is None:
        # without it, daily cannot tell what is loaded and would quietly fetch the month again
        if since is None:
            print(
                "no state file %s: daily cannot tell which days are already loaded. Pass --since YYYY-MM-DD to start "
                "from that day, or run backfill --since YYYY-MM-DD." % (data_dir / STATE_NAME),
                file=sys.stderr,
            )
            return EXIT_CHECKS
        print("note: no state file %s; starting at %s" % (data_dir / STATE_NAME, since.isoformat()), file=sys.stderr)
        state, activity_start = _empty_state(), since
    else:
        if since is not None:
            print("note: the state file exists, so --since is ignored", file=sys.stderr)
        state = found
        loaded = _days(state.get("activity_loaded") or [])
        activity_start = (max(loaded) + timedelta(days=1)) if loaded else month_first
    if _same_second(data_dir, pulled_at):
        return EXIT_CHECKS
    stepper = _Stepper(data_dir, state, "daily")
    yesterday = today - timedelta(days=1)
    loaded_summaries = _days(state.get("summaries_loaded") or [])
    summaries_start = min(max(loaded_summaries) + timedelta(days=1), yesterday) if loaded_summaries else yesterday
    try:
        activity, summaries = _collect(
            client, data_dir, state, stepper, pulled_at,
            report_windows, activity_start, summaries_start,
        )
    except ApiError as exc:
        _write_state(data_dir, state)
        return _api_failed(exc.status)
    except RawFileExists as exc:
        return _raw_exists(exc)
    except ValueError as exc:
        return _unusable("daily", data_dir, state, pulled_at, stepper.raw_files(), exc)
    return _finish("daily", data_dir, state, stepper, pulled_at, activity, summaries, parquet)


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

    # swap: the old tree goes only after the new one is complete
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

    Read from the state file: for ``activity_loaded`` and for ``summaries_loaded``, the last day of the unbroken run of
    loaded days from the first one whose period is final (``is_final_period``: its month ended more than 7 days before
    the pull of the latest published run), and the earlier of the two (``meterline.run.final_through``, contract rule
    R19). So the answer is the last day of a month, or ``None``. No request, no lock; a damaged state file raises
    ``StateError``."""
    state = _read_state(Path(data_dir))
    if state is None:
        return None
    ends = [final_through(data_dir, _days(state.get(key) or []), is_final_period) for key in ("activity_loaded", "summaries_loaded")]
    return None if None in ends else min(e for e in ends if e is not None)


# ---- entry point ------------------------------------------------------------------


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a date as YYYY-MM-DD, got %r" % text) from None


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="where raw, runs, records and state live")
    common.add_argument("--rate-per-minute", type=int, default=50, help="request budget per minute (default 50)")
    common.add_argument("--max-retry-after", type=int, default=MAX_RETRY_AFTER_SECONDS,
                        help="longest wait in seconds a Retry-After header can cause (default 300)")
    common.add_argument("--parquet", action="store_true", help="also write <batch_id>.parquet (needs the parquet extra)")

    parser = argparse.ArgumentParser(
        prog="meterline-anthropic",
        description="Read the Anthropic Enterprise Analytics API and write meterline records. "
        "The key is read from the %s environment variable." % KEY_ENV,
        epilog=SHAPES_NOTE,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    back = sub.add_parser("backfill", parents=[common], help="walk 31-day windows from a start date; resumes after a failure")
    back.add_argument("--since", type=_parse_date, required=True, help="first day to load, YYYY-MM-DD")
    daily = sub.add_parser("daily", parents=[common], help="current month (plus previous month until the 8th), activity since the last day loaded")
    daily.add_argument("--since", type=_parse_date, default=None, help="first day of activity to load; needed only when there is no state file")
    auto = sub.add_parser("auto", parents=[common], help="backfill until a backfill has published, daily afterwards")
    auto.add_argument("--since", type=_parse_date, required=True, help="first day of the backfill; ignored once one has published")
    sub.add_parser("rebuild", parents=[common], help="re-create records from the raw store; no API calls")
    return parser


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_OPTION_DEFAULTS: Dict[str, Any] = {
    "since": None,
    "rate_per_minute": 50,
    "max_retry_after": MAX_RETRY_AFTER_SECONDS,
    "parquet": False,
}


def run(
    command: str,
    *,
    data_dir: Union[str, Path],
    credentials: Union[Mapping[str, str], Callable[[], Mapping[str, str]], None] = None,
    transport: Any = None,
    clock: Callable[[], Any] = time.monotonic,
    sleep: Callable[[Any], Any] = time.sleep,
    now: Optional[Callable[[], datetime]] = None,
    jitter: Callable[[], Any] = random.random,
    wall: Callable[[], Any] = time.time,
    **options: Any,
) -> RunResult:
    """Run one command ("backfill", "daily", "auto" or "rebuild") and return its ``RunResult``.

    ``auto`` runs ``backfill`` until a backfill has published in ``data_dir`` and ``daily`` afterwards
    (``meterline.run.auto_command``); it needs ``since`` (exit 2 without it), which it uses only when it backfills.

    ``options`` are the command-line options under their Python names (``since``, ``rate_per_minute``,
    ``max_retry_after``, ``parquet``) with the command-line defaults. ``credentials`` maps the environment variable
    name to its value, or is a function returning such a mapping; when it is given the environment is not read.
    A run that fails does not raise: ``exit_code`` is what the command line would exit with, after the same messages.
    Everything written under the data directory is readable by its owner only (files 0600, folders 0700), whatever
    the caller's umask: the process umask is 077 for the duration of the command and is put back afterwards."""
    unknown = sorted(set(options) - set(_OPTION_DEFAULTS))
    if unknown:
        raise TypeError("run() got an unexpected option: %s" % ", ".join(unknown))
    if command not in ("backfill", "daily", AUTO, "rebuild"):
        raise ValueError("command must be backfill, daily, auto or rebuild, not %r" % (command,))
    opts = {**_OPTION_DEFAULTS, **options}
    since = opts["since"]
    if isinstance(since, str):
        since = _parse_date(since)
    data_path = Path(data_dir)
    old_umask = os.umask(PRIVATE_UMASK)
    try:
        with data_folder_lock(data_path) as free:
            if not free:
                return RunResult(exit_code=EXIT_TRANSIENT)  # another run holds this data folder (contract rule R17)
            before = run_manifests(data_path)

            def run_one(chosen: str, given: Dict[str, Any]) -> int:
                return _run(
                    chosen, data_path, given.get("since"), opts["rate_per_minute"], opts["max_retry_after"], bool(opts["parquet"]),
                    credentials=credentials, transport=transport, clock=clock, sleep=sleep, now=now, jitter=jitter, wall=wall,
                )

            given = {**opts, "since": since}
            code = run_auto(data_path, given, run_one) if command == AUTO else run_one(command, given)
            return result_of_run(data_path, code, before)
    finally:
        os.umask(old_umask)


def _run(
    command: str,
    data_dir: Path,
    since: Optional[date],
    rate_per_minute: int,
    max_retry_after: int,
    parquet: bool,
    *,
    credentials: Union[Mapping[str, str], Callable[[], Mapping[str, str]], None],
    transport: Any,
    clock: Callable[[], Any],
    sleep: Callable[[Any], Any],
    now: Optional[Callable[[], datetime]],
    jitter: Callable[[], Any],
    wall: Callable[[], Any],
) -> int:
    if command == "rebuild":
        return run_rebuild(data_dir, parquet=parquet)
    if command == "backfill" and since is None:
        raise TypeError("run('backfill') needs the since option")
    if rate_per_minute < 1:
        print("--rate-per-minute must be at least 1", file=sys.stderr)
        return EXIT_CHECKS
    if max_retry_after < 0:
        print("--max-retry-after must not be negative", file=sys.stderr)
        return EXIT_CHECKS
    if credentials is None:
        key = os.environ.get(KEY_ENV, "")
    else:
        given = credentials() if callable(credentials) else credentials
        key = given.get(KEY_ENV) or ""
    if not key:
        print(
            "%s is not set: create an Analytics key (scope read:analytics) and export it in the environment"
            % KEY_ENV,
            file=sys.stderr,
        )
        return EXIT_CHECKS

    clock_now = now if now is not None else _utc_now
    client = AnalyticsClient(
        key,
        transport=transport,
        rate_per_minute=rate_per_minute,
        clock=clock,
        sleep=sleep,
        jitter=jitter,
        wall=wall,
        max_retry_after=max_retry_after,
    )
    with client:
        if command == "backfill":
            return run_backfill(client, data_dir, cast(date, since), now=clock_now, parquet=parquet)
        return run_daily(client, data_dir, now=clock_now, parquet=parquet, since=since)


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
    """Run one command from the command line; see ``run`` for what it does and writes."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    options = {
        "since": getattr(args, "since", None),
        "rate_per_minute": args.rate_per_minute,
        "max_retry_after": args.max_retry_after,
        "parquet": args.parquet,
    }
    result = run(
        args.command, data_dir=args.data_dir, credentials=None, transport=transport,
        clock=clock, sleep=sleep, now=now, jitter=jitter, wall=wall, **options,
    )
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
