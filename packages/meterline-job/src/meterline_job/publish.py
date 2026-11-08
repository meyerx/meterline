"""The ``publish`` step: dated builds, months frozen by copy, and (further down) the ``published`` folder.

Layout of ``gold_root``::

    builds/<YYYY-MM-DD>/, builds/<YYYY-MM-DD>-<n>/   one per run, never overwritten
    frozen/<YYYY-MM>/<audience path>/<dataset>.csv + SHA256SUMS
    frozen.json                                      month -> where its rows came from
    published -> published-<timestamp>/              a symlink, swapped atomically

``builds/``, ``frozen/``, ``frozen.json`` and every ``published-*`` folder are central-only material: they are never
served or copied to a reader. Only the ``published`` folder is.

A month is frozen, and from then on delivered once and never changed, when every source has final data for it and
``freeze_after_days`` have passed; at the latest ``freeze_after_days + freeze_cap_extra_days`` days after its end the next
publish freezes it from the latest build whatever the sources say, and says so (``forced``).

Exit codes of the step: 0 done; 1 the gold-root lock is held (set by the caller); 2 not supported on this platform;
4 any other failure, the previous ``published`` folder staying as it was.

Two things a caller of ``run_publish`` should know. The process umask is set to 077 while the step runs and put back
afterwards: it is process-wide, so another thread of the same process that creates a file in that time gets a private
file too. And no symbolic link is followed: ``assemble`` refuses a link anywhere in the build it copies from or in a
frozen month (exit 4, naming the path), so a link planted in a build cannot pull a file from outside it into ``published``.
"""
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from meterline.run import last_published_pull
from meterline_job.config import PROVIDERS, PUBLISH_DEFAULTS

PRIVATE_UMASK = 0o077  # files 0600, folders 0700
EXIT_PUBLISH = 4
BUILDS, FROZEN = "builds", "frozen"
SUMS = "SHA256SUMS"
MARKER = "FROZEN.json"  # written inside a month's folder before the rename: the entry frozen.json will get
NON_FINAL_DATASETS = ("person_day", "person_month", "pools_month", "groups_month")  # the central files that carry is_final
DEFAULT_AFTER, DEFAULT_CAP_EXTRA = 4, 10
_BUILD_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d+))?$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")


class PublishError(Exception):
    """A publish failure: exit 4, the message says what to look at."""


@dataclass
class Context:
    """What one run tells the step: the UTC day it belongs to, the clock for timestamps, and whether it ran every
    connector step and each succeeded (only such a run freezes a month on the normal rule). ``source_steps`` maps a
    source's name to ``(ok, failure_kind)`` of its connector step in this run; a source that did not run in this
    invocation (a run of one step) is not in it."""
    today: date
    now: Callable[[], datetime]
    run_complete: bool = False
    source_steps: Dict[str, Tuple[bool, str]] = field(default_factory=dict)


@dataclass
class Outcome:
    exit_code: int = 0
    failure_kind: str = "none"
    error: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


# ---- P1: builds -----------------------------------------------------------------------------------------------------


def list_builds(gold_root: Any) -> List[Tuple[Tuple[str, int], Path]]:
    """``[((date, n), folder)]`` of the build folders, oldest first; ``builds/<date>`` is number 1 of its day."""
    root = Path(gold_root) / BUILDS
    found = []
    for path in root.iterdir() if root.is_dir() else []:
        match = _BUILD_NAME.match(path.name)
        if match and path.is_dir():
            found.append(((match.group(1), int(match.group(2) or 1)), path))
    return sorted(found)


def latest_build(gold_root: Any) -> Optional[Path]:
    """The build with the highest (date, number), or ``None``."""
    builds = list_builds(gold_root)
    return builds[-1][1] if builds else None


def next_build(gold_root: Any, day: date) -> Path:
    """The folder the next build of ``day`` goes into: ``builds/<day>`` the first time, then ``-2``, ``-3`` ...

    The number is the highest existing one of that day plus one, compared as a number. An existing folder is never
    handed out."""
    numbers = [n for (d, n), _ in list_builds(gold_root) if d == day.isoformat()]
    name = day.isoformat() if not numbers else "%s-%d" % (day.isoformat(), max(numbers) + 1)
    return Path(gold_root) / BUILDS / name


def clear_leftovers(gold_root: Any) -> None:
    """Remove ``builds/*.tmp``: a killed build leaves one, and gold refuses to build beside it."""
    for path in (Path(gold_root) / BUILDS).glob("*.tmp"):
        shutil.rmtree(path, ignore_errors=True)


# ---- small readers and writers --------------------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError("%s cannot be read: %s" % (what, exc)) from exc


def _write_json(path: Path, obj: Any) -> None:
    """Atomically: a file beside it, then a rename. The caller has set the private umask."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def read_manifest(build: Path) -> Dict[str, Any]:
    manifest = _read_json(build / "manifest.json", "%s/manifest.json" % build.name)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("months"), list):
        raise PublishError("%s/manifest.json lists no months" % build.name)
    return manifest


def read_frozen(gold_root: Any) -> Dict[str, Dict[str, Any]]:
    path = Path(gold_root) / "frozen.json"
    if not path.exists():
        return {}
    frozen = _read_json(path, "frozen.json")
    if not isinstance(frozen, dict):
        raise PublishError("frozen.json is not a mapping of months")
    return frozen


def write_sums(folder: Path) -> None:
    lines = ["%s  %s\n" % (_sha256(p), p.relative_to(folder).as_posix())
             for p in sorted(folder.rglob("*")) if p.is_file() and p.name != SUMS]
    (folder / SUMS).write_text("".join(lines), encoding="utf-8", newline="\n")


def verify_sums(folder: Path) -> None:
    """Every file listed in ``SHA256SUMS`` is there with its hash, and nothing else is in the folder."""
    try:
        listed = [line.partition("  ")[::2] for line in (folder / SUMS).read_text(encoding="utf-8").splitlines()]
    except OSError as exc:
        raise PublishError("%s: %s cannot be read: %s" % (folder.relative_to(folder.parent.parent), SUMS, exc)) from exc
    where = "%s/%s" % (FROZEN, folder.name)
    expected = dict((rel, digest) for digest, rel in listed)
    present = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and p.name != SUMS}
    for rel in sorted(expected):
        path = folder / rel
        if not path.is_file():
            raise PublishError("%s: %s is listed in %s and missing" % (where, rel, SUMS))
        if _sha256(path) != expected[rel]:
            raise PublishError("%s: %s does not match its checksum" % (where, rel))
    extra = sorted(present - set(expected))
    if extra:
        raise PublishError("%s: %s is not listed in %s" % (where, extra[0], SUMS))


def month_end(month: str) -> date:
    first = date(int(month[:4]), int(month[5:7]), 1)
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def settings(config: Dict[str, Any]) -> Tuple[int, int]:
    table = config.get("publish") or {}
    return table.get("freeze_after_days", DEFAULT_AFTER), table.get("freeze_cap_extra_days", DEFAULT_CAP_EXTRA)


def stale_after_days(config: Dict[str, Any]) -> int:
    return (config.get("publish") or {}).get("stale_after_days", PUBLISH_DEFAULTS["stale_after_days"])


# ---- P2: freezing ---------------------------------------------------------------------------------------------------


def connector_complete_through(spec: Dict[str, Any], data_dir: Path) -> Optional[date]:
    """The connector's own ``complete_through(data_dir)``: the last day it holds as final, ``None`` before a backfill."""
    from meterline_job.job import load_connector

    return load_connector(spec).complete_through(data_dir)


def data_complete_through(config: Dict[str, Any]) -> Optional[date]:
    """The earliest ``complete_through`` of the configured connectors; ``None`` when one has none or cannot say.

    A connector whose state cannot be read counts as having no final data: the answer is then the conservative one,
    and the month waits (or is forced by the cap)."""
    return _through([_complete_through(spec, config) for spec in config["connector"]])


def _complete_through(spec: Dict[str, Any], config: Dict[str, Any]) -> Tuple[Optional[date], bool]:
    """``(day, raised)`` for one source: its ``complete_through``, and whether asking raised (then the day is ``None``)."""
    try:
        return connector_complete_through(spec, Path(config["data_root"]) / spec["name"]), False
    except Exception:  # noqa: BLE001 - a damaged state file or a missing package must not stop the publish
        return None, True


def _through(answers: List[Tuple[Optional[date], bool]]) -> Optional[date]:
    days = [day for day, _ in answers]
    return None if not days or None in days else min(days)


def source_status(config: Dict[str, Any], ctx: "Context") -> Tuple[List[Dict[str, Any]], Optional[date], List[str], List[str]]:
    """``(sources, through, stale, unavailable)``: one entry per configured source, the earliest ``complete_through``,
    the names of the stale sources and those whose ``complete_through`` raised.

    A source is stale when its step failed in this run, or its ``complete_through`` is more than ``stale_after_days``
    before the run's day. A source that is not stale by either rule is never reported as stale for a missing answer."""
    limit = stale_after_days(config)
    entries, answers, stale, unavailable = [], [], [], []
    for spec in config["connector"]:
        name = spec["name"]
        day, raised = _complete_through(spec, config)
        answers.append((day, raised))
        if raised:
            unavailable.append(name)
        step = ctx.source_steps.get(name)
        last_ok = last_published_pull(Path(config["data_root"]) / name)
        entries.append({"name": name, "provider": PROVIDERS.get(spec["kind"]),
                        "step_ok": None if step is None else step[0],
                        "last_ok_run": None if last_ok is None else last_ok.astimezone(timezone.utc).isoformat(),
                        "complete_through": None if day is None else day.isoformat(),
                        "failure_kind": None if step is None else step[1]})
        if (step is not None and not step[0]) or (day is not None and (ctx.today - day).days > limit):
            stale.append(name)
    return entries, _through(answers), stale, unavailable


def non_final_months(build: Path) -> set:
    """The months with a row whose is_final is false in the four central datasets that carry it."""
    months = set()
    for stem in NON_FINAL_DATASETS:
        path = build / "central" / (stem + ".csv")
        try:
            with open(path, encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader)
                at_month, at_final = header.index("month"), header.index("is_final")
                months.update(row[at_month] for row in reader if row[at_final] == "false")
        except (OSError, StopIteration, ValueError) as exc:
            raise PublishError("%s/central/%s.csv cannot be read for is_final: %s" % (build.name, stem, exc)) from exc
    return months


def _month_files(build: Path) -> List[Path]:
    """The CSV files of the audience folders that have a ``month`` column; ``delivery/`` and the manifest never."""
    out = []
    for path in sorted(build.rglob("*.csv")):
        rel = path.relative_to(build)
        if rel.parts[0] not in ("central", "exec", "manager", "person"):
            continue
        with open(path, encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle), [])
        if "month" in header:
            out.append(path)
    return out


def _freeze_one(gold_root: Path, build: Path, month: str, entry: Dict[str, Any]) -> None:
    """Write the rows of ``month`` of every month-keyed file of ``build`` to ``frozen/<month>.tmp``, then rename.

    The folder also holds ``FROZEN.json``: the month and the entry ``frozen.json`` gets, written before the checksums are
    made and the folder is renamed, so a crash between the rename and the write of ``frozen.json`` can be recovered."""
    final, tmp = gold_root / FROZEN / month, gold_root / FROZEN / (month + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    for path in _month_files(build):
        with open(path, encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            at = header.index("month")
            rows = [row for row in reader if row[at] == month]
        target = tmp / path.relative_to(build)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
            writer.writerow(header)
            writer.writerows(rows)
    _write_json(tmp / MARKER, dict(entry, month=month))
    write_sums(tmp)
    os.replace(tmp, final)


def _privacy(config: Dict[str, Any], manifest: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """The privacy parameters of the policy file, when that file is the one the build was made with; else ``None``."""
    from meterline_gold import policy_privacy

    try:
        digest, params = policy_privacy(config["gold"]["policy"])
    except Exception:  # noqa: BLE001 - an unreadable policy file must not stop a freeze; the entry then says unknown
        return None
    return params if digest == (manifest.get("inputs") or {}).get("policy_sha256") else None


def recover_frozen(gold_root: Path, frozen: Dict[str, Dict[str, Any]]) -> List[str]:
    """Give the entry back to a month that was renamed into ``frozen/`` and never got its ``frozen.json`` entry.

    That is the one gap of a freeze (a crash between the rename and the write of ``frozen.json``). A folder under
    ``frozen/`` with no entry is recovered when its ``SHA256SUMS`` verifies and its ``FROZEN.json`` parses and names the
    month: the entry is rebuilt from the marker, ``frozen.json`` is written, and ``frozen_entry_recovered <month>`` is
    returned. A folder whose sums or marker fail is not touched and stops the publish (``PublishError``: exit 4)."""
    recovered = []
    root = gold_root / FROZEN
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        month = folder.name
        if not _MONTH.match(month) or not folder.is_dir() or month in frozen:
            continue
        try:
            verify_sums(folder)
            marker = json.loads((folder / MARKER).read_text(encoding="utf-8"))
            if not isinstance(marker, dict) or marker.pop("month", None) != month or "source_build" not in marker:
                raise ValueError("it does not name the month or its source build")
        except (PublishError, OSError, ValueError) as exc:
            raise PublishError("%s/%s exists and frozen.json has no entry for %s, and it cannot be recovered (%s); look at "
                               "it, then move it away or add the entry; a month is never frozen over a folder"
                               % (FROZEN, month, month, exc)) from exc
        frozen[month] = marker
        recovered.append("frozen_entry_recovered " + month)
    if recovered:
        _write_json(gold_root / "frozen.json", frozen)
    return recovered


def freeze_months(config: Dict[str, Any], ctx: Context, build: Path, manifest: Dict[str, Any],
                  frozen: Dict[str, Dict[str, Any]], through: Optional[date]) -> Tuple[List[str], List[str]]:
    """Freeze every month of ``build`` that is due; returns ``(months frozen now, warnings)``.

    A month is due when the first four hold, or when the cap has passed (then ``forced``):
    1. ``through`` (the earliest ``complete_through`` of the connectors) is on or after the last day of the month;
    2. this run ran every connector step and each succeeded;
    3. no row of the month has is_final = false in the central person_day, person_month, pools_month or groups_month;
    4. the day is at least the last day of the month plus ``freeze_after_days``."""
    gold_root = Path(config["gold_root"])
    after, extra = settings(config)
    months = [m for m in manifest["months"] if m not in frozen]
    if not months:
        return [], []
    (gold_root / FROZEN).mkdir(parents=True, exist_ok=True)
    for leftover in (gold_root / FROZEN).glob("*.tmp"):
        shutil.rmtree(leftover, ignore_errors=True)
    open_rows = non_final_months(build)
    since = min((str(spec["since"]) for spec in config["connector"]), default=None)
    done, warnings = [], []
    for month in sorted(months):
        end = month_end(month)
        ready = (through is not None and through >= end and ctx.run_complete and month not in open_rows
                 and ctx.today >= end + timedelta(days=after))
        forced = not ready and ctx.today >= end + timedelta(days=after + extra)
        if not (ready or forced):
            continue
        if (gold_root / FROZEN / month).exists():
            raise PublishError("%s/%s already exists and frozen.json has no entry for %s; look at it, then move it away "
                               "or add the entry; a month is never frozen over a folder" % (FROZEN, month, month))
        entry = {"source_build": build.name, "key_check": manifest.get("key_check"),
                 "policy_sha256": (manifest.get("inputs") or {}).get("policy_sha256"),
                 "schema_version": manifest.get("gold_schema_version"),
                 "frozen_at": ctx.now().astimezone(timezone.utc).isoformat(), "forced": forced,
                 "privacy": _privacy(config, manifest)}
        if month == min(manifest["months"]) and since is not None and since > month + "-01":
            entry["partial_start"] = True  # the data starts after the first day of the first month of the build
        _freeze_one(gold_root, build, month, entry)
        frozen[month] = entry
        _write_json(gold_root / "frozen.json", frozen)  # after each month: a rename done and an entry missing is the one gap
        done.append(month)
        if forced:
            warnings.append("forced_freeze " + month)
            print("warning: forced_freeze %s" % month, file=sys.stderr)
    return done, warnings


# ---- P3: the published folder -----------------------------------------------------------------------------------------

AUDIENCE_FOLDERS = ("central", "exec", "manager", "person")


def is_posix() -> bool:
    return os.name == "posix"


def _read_csv(path: Path) -> Tuple[List[str], List[List[str]]]:
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise PublishError("%s is empty: not even a header" % path.name)
    return rows[0], rows[1:]


def _write_csv(path: Path, header: List[str], rows: List[List[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)
        writer.writerows(rows)


def _copy(src: Path, dst: Path) -> None:
    """A byte-for-byte copy with the mode of the private umask (never ``copy2``: it would carry the source's mode)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def frozen_months_for(audience: str, latest_key: Any, frozen: Dict[str, Dict[str, Any]]) -> List[str]:
    """The frozen months whose rows may be published in a folder of ``audience``.

    ``central`` and ``exec`` show every frozen month (exec carries no keyed id; central keeps the history, so one person
    can appear there under two keys). ``manager`` and ``person`` folders show only the months frozen under the latest
    build's hashing key: a month frozen under another key is not published there, and the latest build's rows of that
    month are not either (every frozen month is delivered once)."""
    if audience in ("manager", "person"):
        return [m for m in sorted(frozen) if frozen[m].get("key_check") == latest_key]
    return sorted(frozen)


def compose_file(build: Path, rel: Path, target: Path, frozen_root: Path, shadowed: set, wanted: List[str],
                 keys: Tuple[str, ...], schema_name: str, parquet: bool) -> None:
    """Write ``rel`` of the published folder: rows of the frozen months in ``wanted`` from ``frozen/``, the rows of every
    other month from the latest build. A file with nothing to replace is copied byte for byte."""
    header, rows = _read_csv(build / rel)
    at = header.index("month")
    kept = [r for r in rows if r[at] not in shadowed]
    added: List[List[str]] = []
    for month in wanted:
        source = frozen_root / month / rel
        if not source.is_file():
            continue
        frozen_header, frozen_rows = _read_csv(source)
        if frozen_header != header:
            raise PublishError("%s/%s: the frozen header differs from the latest build's; migrating frozen rows is out of "
                               "scope" % (FROZEN + "/" + month, rel.as_posix()))
        added.extend(frozen_rows)
    if len(kept) == len(rows) and not added:
        _copy(build / rel, target / rel)
        if parquet:
            _copy((build / rel).with_suffix(".parquet"), (target / rel).with_suffix(".parquet"))
        return
    at_keys = [header.index(c) for c in keys]
    merged = sorted(kept + added, key=lambda r: (tuple(r[i] for i in at_keys), r))  # an empty cell sorts first
    _write_csv(target / rel, header, merged)
    if parquet:
        from meterline_gold import write_parquet_text

        write_parquet_text((target / rel).with_suffix(".parquet"), schema_name, header, merged)


def refuse_links(folder: Path) -> None:
    """Raise ``PublishError`` naming the first symbolic link in ``folder`` (the folder itself, a file or a folder under it).

    Walks with ``lstat`` and never follows a link, so a link to a folder is named and not entered."""
    for root, folders, names in os.walk(folder, followlinks=False):
        for name in sorted(folders + names):
            path = Path(root) / name
            if stat.S_ISLNK(path.lstat().st_mode):
                raise PublishError("%s is a symbolic link; a build or a frozen month holds none, and publish copies none"
                                   % path.relative_to(folder.parent.parent).as_posix())
    if folder.is_symlink():
        raise PublishError("%s is a symbolic link; a build or a frozen month holds none, and publish copies none" % folder.name)


def assemble(config: Dict[str, Any], ctx: Context, build: Path, manifest: Dict[str, Any],
             frozen: Dict[str, Dict[str, Any]], destination: Path) -> None:
    """Build the published folder at ``destination`` (which does not exist) from the latest build and ``frozen/``.

    Refuses (``PublishError``, exit 4) a symbolic link anywhere in the build or in a frozen month before it copies a file."""
    from meterline_gold import dataset_forms

    forms = dataset_forms()
    gold_root = Path(config["gold_root"])
    refuse_links(build)
    for month in sorted(frozen):
        if (gold_root / FROZEN / month).exists() or (gold_root / FROZEN / month).is_symlink():
            refuse_links(gold_root / FROZEN / month)
    shadowed = set(frozen)
    destination.mkdir()
    for path in sorted(build.rglob("*")):
        rel = path.relative_to(build)
        if not path.is_file() or rel.parts[0] == "delivery" or path.suffix == ".parquet":
            continue
        if rel.parts[0] == "manifest.json":
            _copy(path, destination / rel)
            continue
        if rel.parts[0] not in AUDIENCE_FOLDERS or path.suffix != ".csv":
            continue  # a file gold does not write: left out, never guessed
        parquet = path.with_suffix(".parquet").is_file()
        header, _ = _read_csv(path)
        form = forms.get((rel.parts[0], path.stem))
        if "month" not in header or form is None:
            _copy(path, destination / rel)
            if parquet:
                _copy(path.with_suffix(".parquet"), (destination / rel).with_suffix(".parquet"))
            continue
        wanted = frozen_months_for(rel.parts[0], manifest.get("key_check"), frozen)
        compose_file(build, rel, destination, gold_root / FROZEN, shadowed, wanted, form[1], form[0], parquet)


def private_violations(folder: Path) -> List[str]:
    """What is not private in ``folder``: a file with any group or other bit, a folder that is not 0700, a symlink.
    Walks with ``lstat`` and never follows a link."""
    found = []
    for path in [folder, *sorted(folder.rglob("*"))]:
        mode = path.lstat().st_mode
        rel = path.relative_to(folder).as_posix() or "."
        if stat.S_ISLNK(mode):
            found.append("%s is a symlink" % rel)
        elif stat.S_ISDIR(mode):
            if stat.S_IMODE(mode) != 0o700:
                found.append("%s is a folder with mode %o, not 700" % (rel, stat.S_IMODE(mode)))
        elif stat.S_IMODE(mode) & 0o077:
            found.append("%s is a file with mode %o, not private" % (rel, stat.S_IMODE(mode)))
    return found


def _new_folder_name(gold_root: Path, now: datetime) -> str:
    base = "published-" + now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    name, number = base, 1
    while (gold_root / name).exists() or os.path.lexists(gold_root / name):
        number += 1
        name = "%s-%d" % (base, number)
    return name


def _swap(gold_root: Path, name: str) -> Optional[str]:
    """Point ``published`` at ``name`` in one step; returns the name it pointed at before, or ``None``."""
    link, new = gold_root / "published", gold_root / "published.new"
    previous = None
    if os.path.lexists(link):
        if not link.is_symlink():
            raise PublishError("published exists and is not a link; move it away so that publish can make the link")
        previous = os.readlink(link)
    if os.path.lexists(new):
        new.unlink()
    os.symlink(name, new)
    try:
        os.replace(new, link)
    except BaseException:
        if os.path.lexists(new):
            new.unlink()
        raise
    return previous


def _remove_others(gold_root: Path, keep: set) -> None:
    for path in gold_root.glob("published-*"):
        if path.name not in keep:
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path, ignore_errors=True)


def _dump(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def write_freshness(destination: Path, ctx: Context, build: Path, manifest: Dict[str, Any],
                    frozen: Dict[str, Dict[str, Any]], through: Optional[date], warnings: List[str],
                    sources: Optional[List[Dict[str, Any]]] = None, stale: Optional[List[str]] = None) -> None:
    """``central/published.json`` and a ``freshness.json`` in every other audience folder."""
    stamp = ctx.now().astimezone(timezone.utc).isoformat()
    latest_key = manifest.get("key_check")
    policy = {build.name: (manifest.get("inputs") or {}).get("policy_sha256")}
    policy.update({entry["source_build"]: entry.get("policy_sha256") for entry in frozen.values()})
    data_through = None if through is None else through.isoformat()
    _dump(destination / "central" / "published.json", {
        "latest_build": build.name,
        "data_complete_through": data_through,
        "published_at": stamp,
        "frozen_months": sorted(frozen),
        "in_progress_months": [m for m in manifest["months"] if m not in frozen],
        "forced_freezes": sorted(m for m, entry in frozen.items() if entry.get("forced")),
        "key_rotations": sorted(m for m, entry in frozen.items() if entry.get("key_check") != latest_key),
        "policy_sha256": policy,
        "sources": list(sources or []),
        "warnings": list(warnings),
    })
    folders = [(destination / "exec", frozen_months_for("exec", latest_key, frozen))]
    for audience in ("manager", "person"):
        folders += [(path, frozen_months_for(audience, latest_key, frozen)) for path in sorted((destination / audience).glob("*"))]
    for folder, months in folders:
        _dump(folder / "freshness.json", {"published_at": stamp, "frozen_months": months, "data_through": data_through,
                                          "stale_sources": list(stale or [])})


def policy_warnings(config: Dict[str, Any], manifest: Dict[str, Any], frozen: Dict[str, Dict[str, Any]]) -> List[str]:
    """``policy_changed YYYY-MM`` for each frozen month whose privacy parameters (k, exec depth) differ from the latest build's.

    When a frozen month's parameters or the latest build's are not known (the policy file was not the build's), any
    difference in the policy hash counts: more warnings, never fewer."""
    latest_hash = (manifest.get("inputs") or {}).get("policy_sha256")
    current = _privacy(config, manifest)
    out = []
    for month in sorted(frozen):
        old = frozen[month].get("privacy")
        changed = (old != current) if (old is not None and current is not None) else frozen[month].get("policy_sha256") != latest_hash
        if changed:
            out.append("policy_changed " + month)
    return out


def publish_folder(config: Dict[str, Any], ctx: Context, build: Path, manifest: Dict[str, Any],
                   frozen: Dict[str, Dict[str, Any]], through: Optional[date], warnings: List[str],
                   sources: Optional[List[Dict[str, Any]]] = None, stale: Optional[List[str]] = None) -> str:
    """Verify the frozen months, assemble a new ``published-<timestamp>`` folder, check it, swap it in; returns its name.

    Any failure removes the half-made folder and leaves ``published`` as it was."""
    from meterline_gold.write import a09_violations

    gold_root = Path(config["gold_root"])
    for month in sorted(frozen):
        verify_sums(gold_root / FROZEN / month)
    name = _new_folder_name(gold_root, ctx.now())
    destination = gold_root / name
    try:
        assemble(config, ctx, build, manifest, frozen, destination)
        write_freshness(destination, ctx, build, manifest, frozen, through, warnings, sources, stale)
        loose = private_violations(destination)
        if loose:
            raise PublishError("%s is not private: %s" % (name, "; ".join(loose[:3])))
        found = a09_violations(destination, config["gold"]["org"], Path(config["data_root"]) / "silver")
        if found:
            raise PublishError("A09: an identifier reached the published folder: %s" % (found[0],))
        previous = _swap(gold_root, name)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    _remove_others(gold_root, {name, previous} - {None})
    return name


# ---- old builds -----------------------------------------------------------------------------------------------------


def keep_builds(config: Dict[str, Any]) -> int:
    return (config.get("publish") or {}).get("keep_builds", PUBLISH_DEFAULTS["keep_builds"])


def prune_builds(gold_root: Any, keep: int, published_from: Path) -> List[str]:
    """After a successful publish: delete every build under ``builds/`` except the newest ``keep``; return their names.

    The build ``published`` was assembled from (``published_from``) is never deleted. From every kept build other than the
    newest, ``delivery/`` is deleted (it holds the directory with names and addresses); the rest of such a build stays.
    Frozen months do not depend on a build folder, so they are not touched."""
    builds = [folder for _, folder in list_builds(gold_root)]
    kept = builds[-keep:] if keep > 0 else []
    removed = []
    for folder in builds:
        if folder in kept or folder == published_from:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(folder.name)
    for folder in kept[:-1]:
        shutil.rmtree(folder / "delivery", ignore_errors=True)
    return removed


# ---- the step -------------------------------------------------------------------------------------------------------


def run_publish(config: Dict[str, Any], ctx: Context) -> Outcome:
    """Freeze what is due, then publish. Never raises for a publish failure: it returns exit 4 and the message."""
    if not is_posix():
        return Outcome(2, "input", "publish needs a POSIX system (the published folder is a symlink swapped in at once)")
    old_umask = os.umask(PRIVATE_UMASK)
    try:
        return _publish(config, ctx)
    except PublishError as exc:
        return Outcome(EXIT_PUBLISH, "publish", str(exc))
    except OSError as exc:
        return Outcome(EXIT_PUBLISH, "publish", "%s: %s" % (type(exc).__name__, exc))
    finally:
        os.umask(old_umask)


def _publish(config: Dict[str, Any], ctx: Context) -> Outcome:
    gold_root = Path(config["gold_root"])
    build = latest_build(gold_root)
    if build is None:
        raise PublishError("there is no build under %s/%s: run the gold step first" % (gold_root.name, BUILDS))
    manifest = read_manifest(build)
    frozen = read_frozen(gold_root)
    recovered = recover_frozen(gold_root, frozen)
    for month, entry in frozen.items():
        if entry.get("schema_version") != manifest.get("gold_schema_version"):
            raise PublishError("frozen month %s holds schema_version %s rows and the latest build is %s; migrating frozen "
                               "rows is out of scope" % (month, entry.get("schema_version"), manifest.get("gold_schema_version")))
    sources, through, stale, unavailable = source_status(config, ctx)
    done, warnings = freeze_months(config, ctx, build, manifest, frozen, through)
    warnings = (recovered + warnings + policy_warnings(config, manifest, frozen) + ["stale_source " + name for name in stale]
                + ["complete_through_unavailable " + name for name in unavailable])
    folder = publish_folder(config, ctx, build, manifest, frozen, through, warnings, sources, stale)
    pruned = prune_builds(gold_root, keep_builds(config), build)
    return Outcome(extra={"build": build.name, "frozen": done, "warnings": warnings, "published": folder, "pruned": pruned})
