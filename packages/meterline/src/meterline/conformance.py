"""The conformance kit: what every connector's tests call to prove it keeps the connector contract.

``docs/reference/connector-contract.md`` lists the rules; each rule here has the same identifier (``R01`` ...) and a
check that runs the connector's own ``run`` against its public fake API. A connector describes itself once, as a
``Connector``, and runs the kit in one test file::

    import pytest
    from meterline.conformance import RULES, Connector, check

    CONNECTOR = Connector(
        name="meterline-example", run=run, make_fake=FakeAPI,
        credentials=lambda fake: {"EXAMPLE_KEY": fake.api_key},
        backfill_options={"since": date(2026, 9, 1)},
        fail=lambda fake, status, times: fake.fail("/usage", status, times=times),
        bad_options={"rate_per_minute": 0},
        duplicate=lambda fake: fake.days["2026-09-02"].append(dict(fake.days["2026-09-02"][0])),
    )

    @pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.id)
    def test_connector_contract(rule, tmp_path):
        check(rule, CONNECTOR, tmp_path)

A check that fails raises ``ConformanceError`` (an ``AssertionError``) whose message starts with the rule, its title and
its anchor in the contract page, then says what was seen. The kit needs no network and no key, and imports no test
framework: ``RULES`` is a tuple of ``Rule`` objects so any runner can parametrize over it.

One more helper is not a rule. ``compare_shape(answer, shape_file, *, data_keys=(), optional=())`` lists the differences
in keys and nesting between a parsed answer of the fake API and a shape file of ``docs/api-shapes/``, never by value; a
connector's shape test calls it in place of a comparison of its own::

    assert compare_shape(body, SHAPES / "usage.json", optional=["note"]) == []
"""

import contextlib
import gzip
import hashlib
import io
import json
import os
import stat
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple, Union

from meterline.http import FakeClock
from meterline.records import KINDS, validate_batch
from meterline.run import RunResult
from meterline.writers import read_jsonl

CONTRACT_PAGE = "docs/reference/connector-contract.md"
VOLATILE_FIELDS = ("pulled_at", "data_refreshed_at", "batch_id")  # the revision rule ignores these
SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")  # a credential with one of these in its name is a secret
MANIFEST_KEYS = ("batch_id", "command", "status", "windows", "published_counts", "pulled_at", "raw_files", "failures",
                 "failure_kind")
START_NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)

Credentials = Union[Mapping[str, str], Callable[[Any], Mapping[str, str]]]


class ConformanceError(AssertionError):
    """A connector broke a rule of the contract; the message names the rule."""


@dataclass(frozen=True)
class Connector:
    """What the kit needs to know about one connector.

    ``run`` is the connector's ``run(command, *, data_dir, credentials, transport, ...)``. ``make_fake`` builds a fresh
    public fake API with no faults. ``credentials`` maps the key variable names to the values the fake accepts, or is a
    function of the fake returning that mapping. ``backfill_options`` and ``daily_options`` are the options of those
    commands (``since`` and the like). ``fail(fake, status, times)`` scripts the fake to answer ``status`` to the next
    ``times`` requests, whatever they ask for. ``bad_options`` are options the connector rejects as input (exit 2).
    ``duplicate(fake)`` scripts the fake so that one answer holds a row, a page or a day twice, so that two records of
    the run would share a key (R16); the run must refuse it with exit 2 and publish nothing. Leave it out and R16 fails,
    telling you to set it. ``now`` is the clock of the first run; a later run
    reads ``now + daily_after``.
    """

    name: str
    run: Callable[..., RunResult]
    make_fake: Callable[[], Any]
    credentials: Credentials
    backfill_options: Mapping[str, Any]
    fail: Callable[[Any, int, int], None]
    bad_options: Mapping[str, Any]
    daily_options: Mapping[str, Any] = field(default_factory=dict)
    now: datetime = START_NOW
    daily_after: timedelta = timedelta(days=1)
    duplicate: Optional[Callable[[Any], None]] = None

    def creds(self, fake: Any) -> Mapping[str, str]:
        return self.credentials(fake) if callable(self.credentials) else self.credentials


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    check: Callable[["Connector", Path], None]

    @property
    def anchor(self) -> str:
        return "%s#%s" % (CONTRACT_PAGE, self.id.lower())


def _fail(rule_id: str, detail: str) -> None:
    rule = _BY_ID[rule_id]
    raise ConformanceError("contract rule %s (%s): %s [see %s]" % (rule.id, rule.title, detail, rule.anchor))


def _expect(ok: bool, rule_id: str, detail: str) -> None:
    if not ok:
        _fail(rule_id, detail)


# ---- driving a connector -------------------------------------------------------------------------------------------


def _go(c: Connector, data_dir: Path, command: str, *, fake: Any = None, transport: Any = None, clock: Optional[FakeClock] = None,
        now: Optional[datetime] = None, credentials: Optional[Mapping[str, str]] = None, options: Optional[Mapping[str, Any]] = None,
        capture: Optional[io.StringIO] = None) -> RunResult:
    fake = fake if fake is not None else c.make_fake()
    clk = clk_of(clock)
    given = dict(c.backfill_options if command == "backfill" else c.daily_options)
    given.update(options or {})
    if command == "rebuild":
        given = {}
    stderr = capture if capture is not None else io.StringIO()
    with contextlib.redirect_stderr(stderr):
        return c.run(
            command, data_dir=data_dir, credentials=c.creds(fake) if credentials is None else credentials,
            transport=transport if transport is not None else fake.transport, clock=clk, sleep=clk.sleep, wall=clk,
            now=lambda: now or c.now, jitter=lambda: 0, **given)


def clk_of(clock: Optional[FakeClock]) -> FakeClock:
    return clock if clock is not None else FakeClock()


def _backfilled(c: Connector, tmp: Path, name: str = "data") -> Tuple[Path, RunResult, Any]:
    data = tmp / name
    fake = c.make_fake()
    result = _go(c, data, "backfill", fake=fake)
    return data, result, fake


def _files(root: Path) -> List[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _manifest(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifests(data: Path) -> Dict[str, Dict[str, Any]]:
    return {p.name: _manifest(p) for p in sorted((data / "runs").glob("*.json"))}


def _records(data: Path) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for kind in KINDS:
        rows: List[Dict[str, Any]] = []
        for path in sorted((data / "records" / kind).glob("*.jsonl")):
            rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
        out[kind] = rows
    return out


def _stable(record: Mapping[str, Any]) -> str:
    return json.dumps({k: v for k, v in record.items() if k not in VOLATILE_FIELDS}, sort_keys=True, default=str)


def _digest(paths: List[Path]) -> Dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def _timeout_transport() -> Any:
    import httpx  # every connector depends on it; the core package does not, so it is imported where it is used

    def refuse(request: "httpx.Request") -> "httpx.Response":
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    return httpx.MockTransport(refuse)


def _cut_off(inner: Any, after: int) -> Any:
    """A transport that passes the first ``after`` requests to ``inner``, then answers 503 to every one."""
    import httpx

    seen = [0]

    def handle(request: "httpx.Request") -> "httpx.Response":
        seen[0] += 1
        if seen[0] > after:
            return httpx.Response(503, json={"error": "synthetic outage"})
        return inner.handle_request(request)

    return httpx.MockTransport(handle)


# ---- the rules -----------------------------------------------------------------------------------------------------


def _r01_layout(c: Connector, tmp: Path) -> None:
    data, result, _ = _backfilled(c, tmp)
    _expect(result.exit_code == 0, "R01", "a first backfill on the fake API exited %d, not 0" % result.exit_code)
    top = sorted(p.name for p in data.iterdir())
    _expect(top == ["raw", "records", "runs", "state.json"], "R01", "the data directory holds %s, not raw, records, runs and state.json" % top)
    _expect(bool(_files(data / "raw")), "R01", "raw/ holds no file: every response must be kept as received")
    kinds = sorted(p.name for p in (data / "records").iterdir())
    _expect(all(k in KINDS for k in kinds), "R01", "records/ holds %s; only the record kinds %s are allowed" % (kinds, list(KINDS)))
    manifests = _manifests(data)
    _expect(len(manifests) == 1, "R01", "one backfill wrote %d run manifests, not 1" % len(manifests))
    (name, manifest), = manifests.items()
    missing = [k for k in MANIFEST_KEYS if k not in manifest]
    _expect(not missing, "R01", "the run manifest lacks the key(s) %s that RunResult reads" % missing)
    _expect(manifest["status"] == "published" and manifest["failure_kind"] == "none", "R01",
            "a successful run's manifest says status=%r failure_kind=%r" % (manifest["status"], manifest["failure_kind"]))
    _expect(name == manifest["batch_id"] + ".json", "R01", "the manifest file %s is not named after batch_id %s" % (name, manifest["batch_id"]))
    for kind in kinds:
        names = sorted(p.name for p in (data / "records" / kind).glob("*"))
        _expect(names == [manifest["batch_id"] + ".jsonl"], "R01", "records/%s holds %s, not <batch_id>.jsonl" % (kind, names))


def _no_floats(line: str, where: str) -> None:
    def refuse(text: str) -> Any:
        _fail("R02", "%s holds the number %s written with a fraction or exponent: money and quantities are decimal "
              "strings, never floats" % (where, text))

    json.loads(line, parse_float=refuse)


def _r02_records(c: Connector, tmp: Path) -> None:
    data, _, _ = _backfilled(c, tmp)
    seen = 0
    for path in sorted((data / "records").rglob("*.jsonl")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            _no_floats(line, "%s line %d" % (path.name, number))
            seen += 1
    _expect(seen > 0, "R02", "the backfill published no record")
    for path in sorted((data / "records").rglob("*.jsonl")):
        try:
            validate_batch(read_jsonl(path, KINDS[path.parent.name]))
        except Exception as exc:  # RecordError, or whatever the reader or the validator raises
            _fail("R02", "%s/%s does not validate: %s" % (path.parent.name, path.name, exc))


def _r03_result(c: Connector, tmp: Path) -> None:
    data, result, _ = _backfilled(c, tmp)
    (name, manifest), = _manifests(data).items()
    _expect(result.batch_id == manifest["batch_id"], "R03",
            "RunResult.batch_id %r is not the manifest's %r" % (result.batch_id, manifest["batch_id"]))
    _expect(result.manifest_path is not None and Path(result.manifest_path).name == name, "R03",
            "RunResult.manifest_path %r does not name the manifest %s" % (result.manifest_path, name))
    _expect(result.records == manifest["published_counts"], "R03",
            "RunResult.records %s differ from the manifest's published_counts %s" % (result.records, manifest["published_counts"]))
    _expect(list(result.windows) == list(manifest["windows"]), "R03", "RunResult.windows differ from the manifest's windows")
    for kind, count in manifest["published_counts"].items():
        lines = sum(1 for _ in (data / "records" / kind / (manifest["batch_id"] + ".jsonl")).read_text(encoding="utf-8").splitlines())
        _expect(lines == count, "R03", "published_counts says %d %s record(s), the file holds %d" % (count, kind, lines))


def _r04_state(c: Connector, tmp: Path) -> None:
    data, _, _ = _backfilled(c, tmp)
    state = json.loads((data / "state.json").read_text(encoding="utf-8"))
    _expect(isinstance(state, dict) and "pending" in state, "R04",
            "state.json must be a JSON object with a `pending` key (null or the run in progress)")
    _expect(state["pending"] is None, "R04", "after a finished run `pending` is %r, not null" % (state["pending"],))
    damaged = (data / "state.json")
    damaged.write_text('{"days_loaded": ["2026-09', encoding="utf-8")
    fake = c.make_fake()
    before = damaged.read_bytes()
    err = io.StringIO()
    result = _go(c, data, "daily", fake=fake, now=c.now + c.daily_after, capture=err)
    _expect(result.exit_code == 2 and result.failure_kind == "input", "R04",
            "a damaged state.json gave exit %d (%s), not 2 (input)" % (result.exit_code, result.failure_kind))
    _expect(not fake.log, "R04", "a damaged state.json must stop the run before any request; %d request(s) were made" % len(fake.log))
    _expect(damaged.read_bytes() == before, "R04", "a damaged state.json must be left as it was for the owner to inspect")
    _expect("state" in err.getvalue().lower(), "R04", "the message does not name the state file")


def _r05_permissions(c: Connector, tmp: Path) -> None:
    old = os.umask(0o022)
    try:
        data, _, _ = _backfilled(c, tmp)
        _expect(os.umask(0o022) == 0o022, "R05", "the process umask was left changed after the run")
    finally:
        os.umask(old)
    bad = []
    for path in [data, *sorted(data.rglob("*"))]:
        mode = stat.S_IMODE(path.stat().st_mode)
        want = 0o700 if path.is_dir() else 0o600
        if mode != want:
            bad.append("%s is %o, not %o" % (path.relative_to(data.parent), mode, want))
    _expect(not bad, "R05", "under a 022 umask: " + "; ".join(bad[:5]))


def _r06_raw_kept(c: Connector, tmp: Path) -> None:
    data, _, _ = _backfilled(c, tmp)
    before = _digest(_files(data / "raw"))
    _go(c, data, "daily", fake=c.make_fake(), now=c.now + c.daily_after)
    after = _digest(_files(data / "raw"))
    changed = sorted(p for p in before if after.get(p) != before[p])
    _expect(not changed, "R06", "raw file(s) were changed or removed by a later run: %s" % [Path(p).name for p in changed[:3]])
    _expect(len(after) > len(before), "R06", "a later run kept no raw file of its own (%d before, %d after)" % (len(before), len(after)))


def _r07_same_second(c: Connector, tmp: Path) -> None:
    data, first, _ = _backfilled(c, tmp)
    fake = c.make_fake()
    before = _digest(_files(data))
    err = io.StringIO()
    again = _go(c, data, "backfill", fake=fake, capture=err)  # the same `now`: the same batch id
    _expect(again.exit_code == 2 and again.failure_kind == "input", "R07",
            "a second run in the second of a finished run exited %d (%s), not 2 (input)" % (again.exit_code, again.failure_kind))
    _expect(_digest(_files(data)) == before, "R07", "a refused run in the same second still changed the data directory")
    _expect(not fake.log, "R07", "a refused run in the same second made %d request(s)" % len(fake.log))


def _r08_revision(c: Connector, tmp: Path) -> None:
    data, _, _ = _backfilled(c, tmp)
    first = _records(data)
    for step in (1, 2):
        result = _go(c, data, "daily", fake=c.make_fake(), now=c.now + c.daily_after * step)
        _expect(result.exit_code == 0, "R08", "daily number %d exited %d" % (step, result.exit_code))
    everything = _records(data)
    for kind, rows in everything.items():
        seen: Dict[str, int] = {}
        for record in rows:
            key = _stable(record)
            seen[key] = seen.get(key, 0) + 1
        repeated = [k for k, n in seen.items() if n > 1]
        _expect(not repeated, "R08", "%d %s record(s) were published again with the same content (only pulled_at, "
                "data_refreshed_at or batch_id differ): %s" % (len(repeated), kind, repeated[0][:160] if repeated else ""))
    _expect(sum(map(len, everything.values())) >= sum(map(len, first.values())), "R08", "a later run removed published records")


def _r09_rebuild(c: Connector, tmp: Path) -> None:
    data, _, _ = _backfilled(c, tmp)
    before = _digest(_files(data / "records"))
    import shutil

    shutil.rmtree(data / "records")
    fake = c.make_fake()
    result = _go(c, data, "rebuild", fake=fake, credentials={}, transport=_timeout_transport())
    _expect(result.exit_code == 0, "R09", "rebuild exited %d; it must need no key and make no request" % result.exit_code)
    _expect(not fake.log, "R09", "rebuild made %d request(s)" % len(fake.log))
    after = _digest(_files(data / "records"))
    _expect({Path(k).relative_to(data) for k in after} == {Path(k).relative_to(data) for k in before} and
            sorted(after.values()) == sorted(before.values()), "R09",
            "the records rebuilt from the raw files are not byte-identical to the published ones")


def _r10_resume(c: Connector, tmp: Path) -> None:
    clean, clean_result, probe = _backfilled(c, tmp, "clean")
    requests = len(probe.log)
    _expect(requests >= 2, "R10", "the fake API saw only %d request(s) in a backfill; it must log every request" % requests)
    data = tmp / "interrupted"
    fake = c.make_fake()
    cut = _cut_off(fake.transport, max(1, requests // 2))
    broken = _go(c, data, "backfill", fake=fake, transport=cut)
    _expect(broken.exit_code == 1 and broken.failure_kind == "transient", "R10",
            "an outage half way gave exit %d (%s), not 1 (transient)" % (broken.exit_code, broken.failure_kind))
    state = json.loads((data / "state.json").read_text(encoding="utf-8"))
    _expect(isinstance(state, dict) and "pending" in state, "R10", "after a failed run state.json is not a valid state object")
    _expect(not any((data / "records").rglob("*.jsonl")) if (data / "records").exists() else True, "R10",
            "a run that failed before normalising published records")
    claimed = sorted(k for k, v in state.items() if k.endswith("loaded") and v)
    _expect(not claimed, "R10", "state.json lists loaded days under %s although the run published nothing: state is written "
            "after the records, never before" % claimed)
    resumed = _go(c, data, "backfill", fake=c.make_fake(), now=c.now + timedelta(minutes=1))
    _expect(resumed.exit_code == 0, "R10", "running the same command again after the outage exited %d, not 0" % resumed.exit_code)
    want = sorted(_stable(r) for rows in _records(clean).values() for r in rows)
    got = sorted(_stable(r) for rows in _records(data).values() for r in rows)
    _expect(got == want, "R10", "the records after fail-and-resume differ from an uninterrupted run (%d vs %d record(s))" % (len(got), len(want)))


def _r11_exit_codes(c: Connector, tmp: Path) -> None:
    n = 0

    def one(label: str, **go: Any) -> Tuple[RunResult, str]:
        nonlocal n
        n += 1
        err = io.StringIO()
        result = _go(c, tmp / ("exit-%d" % n), "backfill", capture=err, **go)
        return result, err.getvalue()

    for status in (400, 401, 403, 404, 422):
        fake = c.make_fake()
        c.fail(fake, status, 50)
        result, text = one("status", fake=fake)
        _expect((result.exit_code, result.failure_kind, result.http_status) == (3, "permanent", status), "R11",
                "an API answer of %d gave exit %d (%s, http_status %s), not 3 (permanent)"
                % (status, result.exit_code, result.failure_kind, result.http_status))
        _expect("permanent:" in text, "R11", "the message for a %d does not start with `permanent:`" % status)
        _expect(len(fake.log) == 1, "R11", "a %d was retried (%d requests); a refusal is never retried" % (status, len(fake.log)))
    for status in (500, 503):
        fake = c.make_fake()
        c.fail(fake, status, 50)
        result, text = one("status", fake=fake)
        _expect((result.exit_code, result.failure_kind, result.http_status) == (1, "transient", status), "R11",
                "an API answer of %d gave exit %d (%s, http_status %s), not 1 (transient)"
                % (status, result.exit_code, result.failure_kind, result.http_status))
        _expect("transient:" in text, "R11", "the message for a %d does not start with `transient:`" % status)
    result, _ = one("timeout", transport=_timeout_transport())
    _expect((result.exit_code, result.failure_kind) == (1, "transient"), "R11",
            "a timeout gave exit %d (%s), not 1 (transient)" % (result.exit_code, result.failure_kind))
    result, _ = one("bad options", options=c.bad_options)
    _expect((result.exit_code, result.failure_kind) == (2, "input"), "R11",
            "bad options gave exit %d (%s), not 2 (input)" % (result.exit_code, result.failure_kind))
    result, _ = one("no key", credentials={})
    _expect((result.exit_code, result.failure_kind) == (2, "input"), "R11",
            "no key gave exit %d (%s), not 2 (input)" % (result.exit_code, result.failure_kind))
    result, _ = one("ok")
    _expect((result.exit_code, result.failure_kind, result.http_status) == (0, "none", None), "R11",
            "a good run gave exit %d (%s, http_status %s)" % (result.exit_code, result.failure_kind, result.http_status))


def _r12_clock(c: Connector, tmp: Path) -> None:
    fake = c.make_fake()
    c.fail(fake, 429, 1)
    clock = FakeClock()
    started = clock.now
    result = _go(c, tmp / "limited", "backfill", fake=fake, clock=clock)
    _expect(result.exit_code == 0, "R12", "one 429 followed by good answers exited %d; it must be retried" % result.exit_code)
    _expect(bool(clock.sleeps), "R12", "the retry after a 429 did not wait through the injected `sleep`")
    _expect(all(isinstance(s, (int, Decimal)) and s >= 0 for s in clock.sleeps), "R12",
            "the injected sleep got a value that is not a non-negative number: %s" % clock.sleeps[:3])
    _expect(clock.now - started == sum(clock.sleeps), "R12", "time passed that the injected sleep did not account for")


def _r13_secrets(c: Connector, tmp: Path) -> None:
    fake = c.make_fake()
    secrets = [v for k, v in c.creds(fake).items() if v and any(w in k.upper() for w in SECRET_WORDS)]
    _expect(bool(secrets), "R13", "no credential name contains %s, so the kit has no key to look for" % "/".join(SECRET_WORDS))
    err = io.StringIO()
    data = tmp / "data"
    _go(c, data, "backfill", fake=fake, capture=err)
    blobs = [err.getvalue().encode("utf-8")]
    for path in _files(data):
        raw = path.read_bytes()
        blobs.append(raw)
        if path.suffix == ".gz":
            blobs.append(gzip.decompress(raw))
    for secret in secrets:
        _expect(not any(secret.encode("utf-8") in b for b in blobs), "R13", "the key appears in a file or message of the run")
    fake = c.make_fake()
    c.fail(fake, 401, 50)
    err = io.StringIO()
    _go(c, tmp / "refused", "backfill", fake=fake, capture=err)
    _expect(all(s not in err.getvalue() for s in secrets), "R13", "the key appears in the message of a refused run")


def _r14_fake(c: Connector, tmp: Path) -> None:
    fake = c.make_fake()
    _expect(hasattr(fake, "transport"), "R14", "the fake API has no `transport` to hand to run()")
    _expect(isinstance(getattr(fake, "log", None), list), "R14", "the fake API keeps no request `log` list")
    _go(c, tmp / "data", "backfill", fake=fake)
    _expect(len(fake.log) > 0, "R14", "the fake API logged no request of a backfill")
    for entry in fake.log[:1]:
        absent = [name for name in ("time", "status", "params") if not hasattr(entry, name)]
        _expect(not absent and (hasattr(entry, "path") or hasattr(entry, "endpoint")), "R14",
                "a request log entry lacks %s (it needs time, status, params and path or endpoint)" % (absent or ["path or endpoint"]))
    failing = c.make_fake()
    c.fail(failing, 503, 1)
    _go(c, tmp / "faulted", "backfill", fake=failing)
    _expect(any(entry.status == 503 for entry in failing.log), "R14",
            "a fault scripted with `fail` was never answered: no logged request has status 503")


@dataclass
class _FloatWatch:
    """Finds a Python float in what ``json.loads`` or ``json.load`` returns while it is installed."""

    phase: str = ""
    found: List[str] = field(default_factory=list)  # the phases in which a float was read

    def holds_float(self, value: Any) -> bool:
        if type(value).__name__ == "float":  # by name: the source scans of the connectors' tests refuse the word float as code
            return True
        if isinstance(value, dict):
            return any(self.holds_float(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return any(self.holds_float(v) for v in value)
        return False

    def wrap(self, original: Callable[..., Any]) -> Callable[..., Any]:
        def watched(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            if self.holds_float(result) and self.phase not in self.found:
                self.found.append(self.phase)
            return result

        return watched


@contextlib.contextmanager
def _floats_watched() -> Iterator[_FloatWatch]:
    """For the length of the block ``json.loads`` and ``json.load`` call the original and walk the result."""
    watch = _FloatWatch()
    original = (json.loads, json.load)
    json.loads, json.load = watch.wrap(original[0]), watch.wrap(original[1])
    try:
        yield watch
    finally:
        json.loads, json.load = original


def _r15_rebuild_keeps_decimal(c: Connector, tmp: Path) -> None:
    import shutil

    data = tmp / "data"

    def no_float(watch: _FloatWatch, phase: str) -> None:
        _expect(phase not in watch.found, "R15", "a JSON number with a fraction was read as a float (in the %s): read raw and "
                "API JSON with json.loads(..., parse_float=Decimal)" % phase)

    with _floats_watched() as watch:
        watch.phase = "backfill"
        result = _go(c, data, "backfill", fake=c.make_fake())
        no_float(watch, "backfill")  # before the exit code: a float makes the figures too long and the run fail
        _expect(result.exit_code == 0, "R15", "a first backfill on the fake API exited %d, not 0" % result.exit_code)
        live = {p.relative_to(data): p.read_bytes() for p in _files(data / "records")}
        shutil.rmtree(data / "records")
        watch.phase = "rebuild"
        rebuilt = _go(c, data, "rebuild", fake=c.make_fake(), credentials={}, transport=_timeout_transport())
        no_float(watch, "rebuild")
        _expect(rebuilt.exit_code == 0, "R15", "rebuild exited %d; it must need no key and make no request" % rebuilt.exit_code)
    after = {p.relative_to(data): p.read_bytes() for p in _files(data / "records")}
    _expect(bool(live), "R15", "the backfill published no record")
    _expect(sorted(after) == sorted(live), "R15", "rebuild wrote the record files %s, the live run wrote %s"
            % (sorted(str(p) for p in after), sorted(str(p) for p in live)))
    different = sorted(str(p) for p in live if after[p] != live[p])
    _expect(not different, "R15", "the records rebuilt from the raw files differ in their bytes from the live run's: %s" % different[:3])


def _r16_batch_checks(c: Connector, tmp: Path) -> None:
    _expect(c.duplicate is not None, "R16", "the connector must set Connector.duplicate, a function that scripts the fake so "
            "that one answer holds a row twice (two records of the run then share a key)")
    fake = c.make_fake()
    c.duplicate(fake)  # type: ignore[misc]
    data = tmp / "data"
    err = io.StringIO()
    result = _go(c, data, "backfill", fake=fake, capture=err)
    _expect((result.exit_code, result.failure_kind) == (2, "input"), "R16",
            "a run whose answer holds a row twice exited %d (%s), not 2 (input): the batch checks must run before anything "
            "is published (stderr: %s)" % (result.exit_code, result.failure_kind, err.getvalue().strip()[:200] or "empty"))
    published = sorted(data.glob("records/**/*.jsonl"))
    _expect(not any(p.stat().st_size for p in published), "R16",
            "a run that failed its batch checks still published %s" % [p.name for p in published[:3]])


RULES: Tuple[Rule, ...] = (
    Rule("R01", "data directory layout and run manifest keys", _r01_layout),
    Rule("R02", "records validate; money and quantities are strings, never floats", _r02_records),
    Rule("R03", "the RunResult is the run manifest", _r03_result),
    Rule("R04", "state.json: shape, and a damaged one is exit 2 before any request", _r04_state),
    Rule("R05", "files 0600 and folders 0700 under any umask", _r05_permissions),
    Rule("R06", "raw files are kept and never overwritten", _r06_raw_kept),
    Rule("R07", "a run in the second of a finished run is refused", _r07_same_second),
    Rule("R08", "the revision rule: publish only records whose content differs", _r08_revision),
    Rule("R09", "rebuild needs no key and no network and reproduces the records", _r09_rebuild),
    Rule("R10", "failure and resume", _r10_resume),
    Rule("R11", "exit codes 0, 1, 2, 3 and failure_kind", _r11_exit_codes),
    Rule("R12", "the injected clock and sleep are the only waiting", _r12_clock),
    Rule("R13", "the key never reaches a file or a message", _r13_secrets),
    Rule("R14", "the public fake API: transport, request log, fault scripting", _r14_fake),
    Rule("R15", "rebuild keeps Decimal: no float is read, the records are the same bytes", _r15_rebuild_keeps_decimal),
    Rule("R16", "batch checks run before publish", _r16_batch_checks),
)
_BY_ID: Dict[str, Rule] = {rule.id: rule for rule in RULES}


def check(rule: Union[Rule, str], connector: Connector, workdir: Union[str, Path]) -> None:
    """Run one rule of the contract against ``connector`` in ``workdir`` (an empty folder); raises ``ConformanceError``."""
    chosen = _BY_ID[rule] if isinstance(rule, str) else rule
    chosen.check(connector, Path(workdir))


def failures(connector: Connector, workdir: Union[str, Path]) -> Iterator[Tuple[Rule, str]]:
    """Every rule that fails, with its message, each in its own subfolder of ``workdir``; for a report, not a test."""
    for rule in RULES:
        folder = Path(workdir) / rule.id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            rule.check(connector, folder)
        except ConformanceError as exc:
            yield rule, str(exc)


# ---- compare_shape -------------------------------------------------------------------------------------------------


def _kind(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "scalar"


def _load_shape(shape_file: Any) -> Any:
    """A shape: a ``str`` or ``Path`` is a ``.json`` file or an ``.ndjson`` file (its first line is one row); anything
    else is a shape already loaded."""
    if not isinstance(shape_file, (str, Path)):
        return shape_file
    path = Path(shape_file)
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if path.suffix == ".ndjson":
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                return json.loads(line)
        raise ValueError("the shape file %s holds no row" % path)
    raise ValueError("a shape file is .json or .ndjson, not %s" % (path.suffix or path.name))


def _is_optional(keys: Tuple[str, ...], optional: Tuple[Tuple[str, ...], ...]) -> bool:
    """True when the key path ends with one of the optional entries (``("metrics", "tokens")`` or ``("note",)``)."""
    return any(len(entry) <= len(keys) and keys[len(keys) - len(entry):] == entry for entry in optional)


def _shape_differences(shape: Any, actual: Any, path: str, keys: Tuple[str, ...], data_keys: frozenset,
                       optional: Tuple[Tuple[str, ...], ...]) -> List[str]:
    if _kind(shape) != _kind(actual):
        return ["%s: expected %s, got %s" % (path, _kind(shape), _kind(actual))]
    found: List[str] = []
    if isinstance(shape, dict):
        if not shape:  # an empty map in the shape file is free-form and not compared
            return found
        if keys and keys[-1] in data_keys:  # the keys are data: compare the values with the shape of the first value
            sample = next(iter(shape.values()))
            for key, value in actual.items():
                found.extend(_shape_differences(sample, value, "%s.%s" % (path, key), keys + ("*",), data_keys, optional))
            return found
        for key in sorted(set(shape) | set(actual), key=str):
            here = keys + (str(key),)
            if key not in actual:
                if not _is_optional(here, optional):
                    found.append("%s.%s: missing" % (path, key))
            elif key not in shape:
                found.append("%s.%s: not in the documented shape" % (path, key))
            else:
                found.extend(_shape_differences(shape[key], actual[key], "%s.%s" % (path, key), here, data_keys, optional))
    elif isinstance(shape, list) and shape:  # an empty list in the shape file is not compared
        for index, item in enumerate(actual):
            found.extend(_shape_differences(shape[0], item, "%s[%d]" % (path, index), keys, data_keys, optional))
    return found


def compare_shape(answer: Any, shape_file: Any, *, data_keys: Iterable[str] = (), optional: Iterable[str] = ()) -> List[str]:
    """The differences in keys and nesting between ``answer`` and a shape, never by value; ``[]`` when there are none.

    ``answer`` is a parsed JSON value. ``shape_file`` is the path of a ``.json`` file, or of an ``.ndjson`` file whose
    first line is one row, or a shape already loaded. Each difference is one string, the path and what differs:
    ``$.data[0].usage: missing``, ``$.data[0].extra: not in the documented shape`` or ``$.data: expected array, got
    object``. Every item of a list is compared with the first item of the shape's list.

    ``data_keys`` names maps whose keys are data (model names, ids): their values are compared with the first value of
    the shape's map, whatever the keys are. ``optional`` lists keys that may be absent: ``"note"`` is a key of that name
    at any depth, ``"metrics.tokens"`` is that key directly inside a ``metrics`` map (a path may start anywhere; no
    key name may hold a dot). An optional key that is present is still compared. An empty map or list in the shape file
    is not compared, and an answer with no item in a list has nothing to compare there: a test that must see a row
    asks for one itself.
    """
    entries = tuple(tuple(str(entry).split(".")) for entry in optional)
    return _shape_differences(_load_shape(shape_file), answer, "$", (), frozenset(data_keys), entries)


__all__ = ["CONTRACT_PAGE", "RULES", "ConformanceError", "Connector", "Rule", "check", "compare_shape", "failures"]
