"""The daily job: run each configured connector, stage silver, build gold, and say what happened as JSON lines.

One JSON object per line on stdout, one per step: ``{"step": "claude", "command": "daily", "ok": true, "exit_code": 0,
"failure_kind": "none", "attempts": 1, ...}``. The steps are the connectors in the order of the file, then ``silver``,
then ``gold``. A failed connector does not stop the others; silver and gold then run on whatever exists. Keys come from
each connector's environment variables, never from the configuration file.

What the job decides, from the exit code of each step (docs/reference/connector-contract.md for the connectors):

- each connector runs its ``auto`` command: ``backfill`` until a backfill has published (a run manifest with ``command``
  backfill and ``status`` published), then ``daily``. A ``state.json`` is not enough: a backfill that failed half way
  leaves one behind. The job reads the choice first (``meterline.run.auto_command``) and writes it as the step line's
  ``command``; the connector makes the same choice under its folder lock and records ``requested`` ``auto``;
- exit 1 (transient) of a connector is retried, once by default, after ``retry_wait_seconds`` (300 by default); nothing
  else is retried, and gold never is;
- exit 2 (input) and exit 3 (permanent) are not retried. The step line carries an ``alert`` that says what to fix;
- exit 4 is the job's own: gold's build failed an assertion (or any error other than a wrong input), or the publish step
  failed. It is on the step line (``failure_kind`` ``build`` for gold, ``publish`` for publish) and in the job's exit
  code only; the connectors' exit codes stay 0 to 3. A publish skipped because gold failed in the same run carries
  ``failure_kind`` ``skipped`` and gold's exit code, and its error names gold;
- the job's exit code is the worst step in the order 0 < 1 < 2 < 3 < 4. A scheduler alerts on a non-zero code and a
  person acts on 2, 3 and 4.

Silver, gold and publish run under one hold of ``<gold_root>/.lock``, taken before silver is staged. A second run that
finds it held writes nothing there and says so on those three step lines (exit 1, transient). The job needs file locks:
a platform without ``fcntl``, or a gold root where the lock file cannot be made, stops it with exit 2 before any step.

Every line the job writes on stdout is one JSON object. What a connector prints for a person goes to stderr.
"""
import contextlib
import importlib
import json
import os
import shutil
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from meterline.run import LockUnavailable, auto_command, data_folder_lock, file_lock, lock_problem
from meterline_job import publish as publish_module
from meterline_job.config import KINDS, RESERVED_STEPS, missing_extra_message

NOT_OPTIONS = {"name", "kind", "since", "retries", "retry_wait_seconds"}
DEFAULT_RETRIES = 1  # extra attempts after a transient failure
DEFAULT_RETRY_WAIT_SECONDS = 300
PRIVATE_UMASK = 0o077  # files 0600, folders 0700
RECORD_KINDS = ("usage", "activity", "seats", "seat_assignments", "org_totals")
EXIT_BUILD = 4  # gold's BuildFailure or any other gold error: the job's own code, not a connector's

ALERTS = {
    "input": "needs a person: the input is wrong (arguments, key, state file or data); running again fails the same way",
    "permanent": "needs a person: the provider refused the request; fix the key, its scope or the identifier, then run again",
    "build": "needs a person: the gold build failed; the error says which check; gold is not run again by the job",
    "publish": "needs a person: the publish step failed; the error says which check; the previous published folder is unchanged",
}


class MissingExtra(Exception):
    """A configured connector's package is not installed; the message names the extra that installs it."""


def emit(out: Any, **line: Any) -> None:
    out.write(json.dumps(line, sort_keys=True) + "\n")
    out.flush()


def step_line(step: str, command: str, exit_code: int, failure_kind: str, **more: Any) -> Dict[str, Any]:
    """A status line with every key a connector's line carries; ``more`` adds or replaces keys."""
    line: Dict[str, Any] = dict(step=step, command=command, ok=exit_code == 0, exit_code=exit_code, failure_kind=failure_kind,
                                http_status=None, attempts=1, batch_id=None, records={}, skipped=[])
    line.update(more)
    if failure_kind in ALERTS:
        line["alert"] = "%s: %s" % (failure_kind, ALERTS[failure_kind])
    return line


def load_connector(spec: Dict[str, Any]) -> Any:
    try:
        return importlib.import_module(KINDS[spec["kind"]][0])
    except ImportError as exc:
        raise MissingExtra(missing_extra_message(spec["kind"], spec["name"])) from exc


def run_connector(spec: Dict[str, Any], data_root: Any, transports: Any, now: Any, sleep: Any = None) -> Tuple[str, Any]:
    """Run the connector's ``auto`` command; returns ``(command, RunResult)``, ``command`` being what auto chooses.

    ``auto`` backfills from ``since`` until a backfill has published and runs ``daily`` afterwards; the job reads that
    choice with ``meterline.run.auto_command`` before the run, for the step line, and has no rule of its own."""
    module = load_connector(spec)
    data_dir = Path(data_root) / spec["name"]
    options = {k: v for k, v in spec.items() if k not in NOT_OPTIONS}
    command = auto_command(data_dir)
    hooks = {} if sleep is None else {"sleep": sleep}  # a test passes a no-op so a retried failure does not wait
    with contextlib.redirect_stdout(sys.stderr):  # what a connector prints for a person is not a JSON line
        result = module.run("auto", data_dir=data_dir, transport=(transports or {}).get(spec["name"]), now=now,
                            since=date.fromisoformat(str(spec["since"])), **hooks, **options)
    return command, result


def run_step(spec: Dict[str, Any], data_root: Any, transports: Any, now: Any, sleep: Any,
             retries: Optional[int] = None) -> Tuple[Dict[str, Any], int]:
    """Run one connector with the retry rule; returns the status line (a dict) of the step and its exit code."""
    wait = spec.get("retry_wait_seconds", DEFAULT_RETRY_WAIT_SECONDS)
    allowed = spec.get("retries", DEFAULT_RETRIES) if retries is None else retries
    attempts, command, result = 0, None, None
    while True:
        attempts += 1
        command, result = run_connector(spec, data_root, transports, now, sleep)
        if result.failure_kind != "transient" or attempts > allowed:
            break
        (sleep or time.sleep)(wait)  # only a transient failure is worth waiting for; the same command runs again
    line = dict(step=spec["name"], command=command, ok=result.ok, exit_code=result.exit_code,
                failure_kind=result.failure_kind, http_status=result.http_status, attempts=attempts,
                batch_id=result.batch_id, records=result.records, skipped=list(result.skipped),
                records_total=sum(result.records.values()))
    if result.ok and line["records_total"] == 0:
        line["empty"] = True  # a run that published nothing: an empty first backfill is not a backfill (rule R18)
    if result.failure_kind in ALERTS:
        line["alert"] = "%s: %s" % (result.failure_kind, ALERTS[result.failure_kind])
    return line, result.exit_code


def connector_step(spec: Dict[str, Any], data_root: Any, transports: Any, now: Any, sleep: Any, retries: Optional[int]) -> Tuple[Dict[str, Any], int]:
    """``run_step`` that turns what a connector cannot do into a line: a missing package or a bad option is an input error."""
    name = spec["name"]
    try:
        return run_step(spec, data_root, transports, now, sleep, retries)
    except MissingExtra as exc:
        line = step_line(name, "run", 2, "input", error=str(exc))
        line["alert"] = "input: needs a person: %s" % exc
    except TypeError as exc:
        if "got an unexpected option" not in str(exc):
            raise
        line = step_line(name, "run", 2, "input", error="%s (a key of this [[connector]] table the connector lacks)" % exc)
    return line, 2


def stage_silver(data_root: Any, names: List[str], staging: Any, locked: Optional[List[str]] = None) -> Path:
    """Copy every connector's published records into one silver folder; gold reads records/<kind>/*.jsonl.

    Each connector's folder is copied under its lock, one after the other. A connector whose folder another run holds is
    not copied: its name is added to ``locked`` (when given) and the copy goes on with the others.

    The records hold e-mail addresses, so the copies are readable by their owner only (files 0600, folders 0700),
    whatever the umask and whatever mode the operator gave the data root: the process umask is 077 while they are
    made and is put back afterwards."""
    old_umask = os.umask(PRIVATE_UMASK)
    try:
        shutil.rmtree(staging, ignore_errors=True)
        for name in names:
            if not (Path(data_root) / name).is_dir():
                continue  # the connector has not run yet: nothing to copy, and no lock file to make in a folder that is not there
            with data_folder_lock(Path(data_root) / name, required=True) as free:
                if not free:
                    if locked is not None:
                        locked.append(name)
                    continue
                _copy_records(data_root, name, staging)
    finally:
        os.umask(old_umask)
    return Path(staging)


def _copy_records(data_root: Any, name: str, staging: Any) -> None:
    for kind in RECORD_KINDS:
        for path in sorted((Path(data_root) / name / "records" / kind).glob("*.jsonl")):
            target = Path(staging) / "records" / kind / ("%s-%s" % (name, path.name))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def silver_step(config: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """Stage silver. A connector whose folder is locked is skipped: exit 1, and the line names it."""
    data_root = Path(config["data_root"])
    locked: List[str] = []
    try:
        silver = stage_silver(data_root, [c["name"] for c in config["connector"]], data_root / "silver", locked)
    except LockUnavailable as exc:
        return step_line("silver", "stage", 2, "input", error=str(exc)), 2
    except OSError as exc:
        return step_line("silver", "stage", 1, "transient", error="%s: %s" % (type(exc).__name__, exc)), 1
    if locked:
        error = "the data folder of %s is in use by another run; its records were not copied" % ", ".join(locked)
        return step_line("silver", "stage", 1, "transient", error=error, skipped=locked, folder=str(silver)), 1
    return step_line("silver", "stage", 0, "none", folder=str(silver)), 0


def data_through(folder: Path) -> Optional[str]:
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8")).get("data_through")
    except (OSError, ValueError):
        return None


def gold_step(config: Dict[str, Any], today: date) -> Tuple[Dict[str, Any], int]:
    """Build a new gold folder under ``builds/``: ``builds/<day>`` the first time that day, then ``-2``, ``-3`` ...

    Never retried: a failed build is a person's to read (exit 4), a wrong input is exit 2. The caller holds the gold-root
    lock; a build is never written over an existing folder."""
    gold_root = Path(config["gold_root"])
    publish_module.clear_leftovers(gold_root)
    folder = publish_module.next_build(gold_root, today)
    return _build_gold(config, folder, dict(build=folder.name, retry=False, data_through=None))


def _build_gold(config: Dict[str, Any], folder: Path, extra: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    import meterline_gold
    from meterline_gold.errors import InputError

    gold = config["gold"]
    try:
        built = meterline_gold.build(silver=Path(config["data_root"]) / "silver", org=gold["org"], policy=gold["policy"],
                                     prices=gold["prices"], out=folder, invoice=gold.get("invoice"),
                                     parquet=gold.get("parquet", True),
                                     key=os.environ.get("METERLINE_GOLD_KEY", "").encode("utf-8") or None)
    except InputError as exc:
        return step_line("gold", "build", 2, "input", error="%s: %s" % (type(exc).__name__, exc), **extra), 2
    except Exception as exc:  # BuildFailure, or anything else gold raises: the build failed
        return step_line("gold", "build", EXIT_BUILD, "build", error="%s: %s" % (type(exc).__name__, exc), **extra), EXIT_BUILD
    extra["data_through"] = data_through(Path(built))
    return step_line("gold", "build", 0, "none", folder=str(built), **extra), 0


def publish_step(config: Dict[str, Any], ctx: "publish_module.Context") -> Tuple[Dict[str, Any], int]:
    """Freeze the months that are due and (see ``meterline_job.publish``) publish; the status line carries the result."""
    outcome = publish_module.run_publish(config, ctx)
    if outcome.exit_code:
        return step_line("publish", "publish", outcome.exit_code, outcome.failure_kind, error=outcome.error), outcome.exit_code
    return step_line("publish", "publish", 0, "none", **outcome.extra), 0


def run_job(config: Dict[str, Any], *, transports: Any = None, now: Any = None, sleep: Any = None, out: Any = None,
            step: Optional[str] = None, no_retry: bool = False, today: Optional[date] = None) -> int:
    """Run the job (or the one ``step``); returns the exit code: 0, or the worst failure (1 transient, 2 input, 3 permanent, 4 build).

    ``now`` is the clock (a function returning an aware datetime) and ``today`` the UTC day the run belongs to, which dates
    the build and decides what is frozen; it defaults to the day of the clock. Both exist so a test can name the day."""
    out = out or sys.stdout
    clock = now or (lambda: datetime.now(timezone.utc))
    day = today or clock().astimezone(timezone.utc).date()
    data_root = Path(config["data_root"])
    names = [c["name"] for c in config["connector"]]
    if step is not None and step not in names + list(RESERVED_STEPS):
        emit(out, **step_line(step, "run", 2, "input", error="no such step %r; the steps are: %s" % (step, ", ".join(names + list(RESERVED_STEPS)))))
        return 2
    tail = [name for name in ("silver", "gold", "publish") if step in (None, name)]
    problem = lock_problem(Path(config["gold_root"]) / ".lock" if tail else None)
    if problem:  # the job's locks fail closed: no lock, no run, and nothing has been done yet
        emit(out, **step_line("job", "start", 2, "input", error=problem))
        return 2
    worst, connector_codes, source_steps = 0, [], {}
    for spec in config["connector"]:
        if step not in (None, spec["name"]):
            continue
        try:
            line, code = connector_step(spec, data_root, transports, clock, sleep, 0 if no_retry else None)
        except Exception as exc:  # a broken connector must not stop the others
            line, code = step_line(spec["name"], "run", 1, "transient", error="%s: %s" % (type(exc).__name__, exc)), 1
        emit(out, **line)
        worst = max(worst, code)
        connector_codes.append(code)
        source_steps[spec["name"]] = (code == 0, line["failure_kind"])
    if tail:
        worst = max(worst, _silver_gold_publish(config, tail, day, clock, out, step is None and not any(connector_codes),
                                                source_steps))
    return worst


def _silver_gold_publish(config: Dict[str, Any], tail: List[str], day: date, clock: Any, out: Any, connectors_ok: bool,
                         source_steps: Optional[Dict[str, Tuple[bool, str]]] = None) -> int:
    """The silver, gold and publish steps under one hold of ``<gold_root>/.lock``, so no other run's silver, build or
    publication lands between them. ``connectors_ok``: the run was whole and every connector step succeeded."""
    worst = 0
    try:
        with file_lock(Path(config["gold_root"]) / ".lock", "gold folder", required=True) as free:
            if not free:
                for name in tail:
                    what = {"silver": "nothing was staged", "gold": "nothing was built", "publish": "nothing was published"}[name]
                    more = dict(build=None, retry=False, data_through=None) if name == "gold" else {}
                    emit(out, **step_line(name, {"silver": "stage", "gold": "build", "publish": "publish"}[name], 1, "transient",
                                          error="the gold folder is in use by another run; %s" % what, **more))
                return 1
            silver_code = 0
            if "silver" in tail:
                line, silver_code = silver_step(config)
                emit(out, **line)
                worst = silver_code
            built_code = 0
            if "gold" in tail:
                line, built_code = gold_step(config, day)
                emit(out, **line)
                worst = max(worst, built_code)
            if "publish" in tail:
                if built_code:
                    line = step_line("publish", "publish", built_code, "skipped",
                                     error="gold failed in this run (exit %d); nothing was published" % built_code)
                    code = built_code
                else:
                    line, code = publish_step(config, publish_module.Context(today=day, now=clock,
                                                                              run_complete=connectors_ok and not silver_code,
                                                                              source_steps=dict(source_steps or {})))
                emit(out, **line)
                worst = max(worst, code)
    except LockUnavailable as exc:
        emit(out, **step_line("job", "lock", 2, "input", error=str(exc)))
        return max(worst, 2)
    return worst
