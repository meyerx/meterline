"""The daily job: run each configured connector, then build gold, and say what happened as JSON lines.

    python examples/03-daily-job/job.py job.toml

One JSON object per line on stdout, one per step: ``{"step": "claude", "command": "daily", "ok": true, "exit_code": 0,
"failure_kind": "none", "attempts": 1, ...}``. A failed connector does not stop the others; the gold step then runs on
whatever silver exists. Keys come from each connector's environment variables, never from the configuration file.

What the job decides, from the connector's exit code (docs/reference/connector-contract.md):

- the command is ``backfill`` until a backfill has published (a run manifest with ``command`` backfill and ``status``
  published), then ``daily``. A ``state.json`` is not enough: a backfill that failed half way leaves one behind;
- exit 1 (transient) is retried, once by default, after ``retry_wait_seconds`` (300 by default); nothing else is retried;
- exit 2 (input) and exit 3 (permanent) are not retried. The step line carries an ``alert`` that says what to fix, and
  the job's exit code is the worst of the steps: 0 all well, 1 a transient failure, 2 an input error, 3 a permanent
  refusal. A scheduler alerts on a non-zero code and a person acts on 2 and 3.
"""
import argparse
import importlib
import json
import os
import shutil
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10: pip install tomli
    import tomli as tomllib

KINDS = {"anthropic": "meterline_anthropic", "anthropic_admin": "meterline_anthropic_admin",
         "litellm": "meterline_litellm", "copilot": "meterline_copilot"}
NOT_OPTIONS = {"name", "kind", "since", "retries", "retry_wait_seconds"}
DEFAULT_RETRIES = 1  # extra attempts after a transient failure
DEFAULT_RETRY_WAIT_SECONDS = 300
PRIVATE_UMASK = 0o077  # files 0600, folders 0700
RECORD_KINDS = ("usage", "activity", "seats", "seat_assignments", "org_totals")


def emit(out, **line):
    out.write(json.dumps(line, sort_keys=True) + "\n")
    out.flush()


def backfill_published(data_dir):
    """True when a backfill has published into ``data_dir``: some run manifest says so.

    ``state.json`` does not say it. A backfill that stopped on a 503 writes one, so it proves only that a run started."""
    runs = Path(data_dir) / "runs"
    for path in sorted(runs.glob("*.json")) if runs.is_dir() else []:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if manifest.get("command") == "backfill" and manifest.get("status") == "published":
            return True
    return False


def run_connector(spec, data_root, transports, now, sleep=None):
    """Backfill until a backfill has published, daily afterwards; returns ``(command, RunResult)``."""
    module = importlib.import_module(KINDS[spec["kind"]])
    data_dir = Path(data_root) / spec["name"]
    options = {k: v for k, v in spec.items() if k not in NOT_OPTIONS}
    if backfill_published(data_dir):
        command, extra = "daily", {}
    else:
        command, extra = "backfill", {"since": date.fromisoformat(spec["since"])}
    hooks = {} if sleep is None else {"sleep": sleep}  # a test passes a no-op so a retried failure does not wait
    result = module.run(command, data_dir=data_dir, transport=(transports or {}).get(spec["name"]), now=now,
                        **hooks, **extra, **options)
    return command, result


ALERTS = {
    "input": "needs a person: the input is wrong (arguments, key, state file or data); running again fails the same way",
    "permanent": "needs a person: the provider refused the request; fix the key, its scope or the identifier, then run again",
}


def run_step(spec, data_root, transports, now, sleep):
    """Run one connector with the retry rule; returns the status line (a dict) of the step."""
    wait = spec.get("retry_wait_seconds", DEFAULT_RETRY_WAIT_SECONDS)
    attempts, command, result = 0, None, None
    while True:
        attempts += 1
        command, result = run_connector(spec, data_root, transports, now, sleep)
        if result.failure_kind != "transient" or attempts > spec.get("retries", DEFAULT_RETRIES):
            break
        (sleep or time.sleep)(wait)  # only a transient failure is worth waiting for; the same command runs again
    line = dict(step=spec["name"], command=command, ok=result.ok, exit_code=result.exit_code,
                failure_kind=result.failure_kind, http_status=result.http_status, attempts=attempts,
                batch_id=result.batch_id, records=result.records, skipped=list(result.skipped))
    if result.failure_kind in ALERTS:
        line["alert"] = "%s: %s" % (result.failure_kind, ALERTS[result.failure_kind])
    return line, result.exit_code


def stage_silver(data_root, names, staging):
    """Copy every connector's published records into one silver folder; gold reads records/<kind>/*.jsonl.

    The records hold e-mail addresses, so the copies are readable by their owner only (files 0600, folders 0700),
    whatever the umask and whatever mode the operator gave the data root: the process umask is 077 while they are
    made and is put back afterwards."""
    old_umask = os.umask(PRIVATE_UMASK)
    try:
        shutil.rmtree(staging, ignore_errors=True)
        for name in names:
            for kind in RECORD_KINDS:
                for path in sorted((Path(data_root) / name / "records" / kind).glob("*.jsonl")):
                    target = Path(staging) / "records" / kind / ("%s-%s" % (name, path.name))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
    finally:
        os.umask(old_umask)
    return Path(staging)


def run_job(config, *, transports=None, now=None, sleep=None, out=sys.stdout):
    """Run the whole job; returns the exit code: 0, or the worst failure of a step (1 transient, 2 input, 3 permanent)."""
    clock = now or (lambda: datetime.now(timezone.utc))
    data_root = Path(config["data_root"])
    worst = 0
    for spec in config["connector"]:
        step = spec["name"]
        try:
            line, code = run_step(spec, data_root, transports, clock, sleep)
            emit(out, **line)
        except Exception as exc:  # a broken connector must not stop the others
            code = 1
            emit(out, step=step, ok=False, failure_kind="transient", error="%s: %s" % (type(exc).__name__, exc))
        worst = max(worst, code)
    try:
        import meterline_gold

        gold = config["gold"]
        silver = stage_silver(data_root, [c["name"] for c in config["connector"]], data_root / "silver")
        folder = Path(config["gold_root"]) / clock().date().isoformat()
        shutil.rmtree(folder, ignore_errors=True)
        built = meterline_gold.build(silver=silver, org=gold["org"], policy=gold["policy"], prices=gold["prices"],
                                     out=folder, key=os.environ.get("METERLINE_GOLD_KEY", "").encode("utf-8") or None)
        emit(out, step="gold", ok=True, folder=str(built))
    except Exception as exc:
        worst = max(worst, 1)
        emit(out, step="gold", ok=False, error="%s: %s" % (type(exc).__name__, exc))
    return worst


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("config", help="the TOML configuration (see job.example.toml)")
    args = ap.parse_args(argv)
    with open(args.config, "rb") as handle:
        return run_job(tomllib.load(handle))


if __name__ == "__main__":
    sys.exit(main())
