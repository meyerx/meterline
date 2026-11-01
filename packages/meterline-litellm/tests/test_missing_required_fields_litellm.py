"""F-06: a required metric that is missing or mistyped in a proxy response
fails the run (exit 2, naming the endpoint and the field) and publishes
nothing. It is never counted as zero.

A missing metric is built with the fake proxy's ``omit_metrics`` (it drops the
key from every ``metrics`` object); a mistyped one by rewriting the answer.
"""

import json
from pathlib import Path

import httpx
import pytest

from litellm_helpers import backfill, basic_rows, users

METRICS = (
    "spend", "flat_cost", "prompt_tokens", "completion_tokens", "cache_read_input_tokens",
    "cache_creation_input_tokens", "api_requests", "successful_requests", "failed_requests",
)
KEY = "sk-synthetic-not-a-real-key"


def _no_records(data_dir):
    return not list(Path(data_dir).glob("records/*/*.jsonl"))


def _failed_manifests(data_dir):
    found = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((Path(data_dir) / "runs").glob("*.json"))]
    return [m for m in found if m["status"] == "failed"]


def _all_metrics(body):
    for result in body.get("results") or []:
        if isinstance(result.get("metrics"), dict):
            yield result["metrics"]
        for model in (result.get("breakdown") or {}).get("models", {}).values():
            yield model["metrics"]


def _rewriting(proxy, edit):
    inner = proxy.transport

    def handler(request: httpx.Request) -> httpx.Response:
        response = inner.handle_request(request)
        if response.status_code != 200 or "daily/activity" not in request.url.path:
            return response
        body = json.loads(response.read().decode("utf-8"))
        edit(body)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _run(proxy, transport, fake_clock, data_dir):
    proxy.transport = transport
    return backfill(data_dir, proxy, fake_clock)


@pytest.mark.parametrize("name", METRICS)
def test_missing_required_field_litellm(name, make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"), omit_metrics=(name,))
    rc = backfill(data_dir, proxy, fake_clock)
    err = capsys.readouterr().err
    assert rc == 2
    assert "user_daily_activity" in err
    assert name in err
    assert "Traceback" not in err
    assert _no_records(data_dir)
    assert _failed_manifests(data_dir)


@pytest.mark.parametrize(
    "name,value",
    [("prompt_tokens", "12"), ("prompt_tokens", None), ("completion_tokens", 1.5), ("api_requests", True),
     ("spend", "0.5"), ("spend", None)],
)
def test_missing_required_field_litellm_wrong_type(name, value, make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))

    def edit(body):
        for metrics in _all_metrics(body):
            metrics[name] = value

    rc = _run(proxy, _rewriting(proxy, edit), fake_clock, data_dir)
    err = capsys.readouterr().err
    assert rc == 2
    assert name in err
    assert "Traceback" not in err
    assert _no_records(data_dir)


@pytest.mark.parametrize("what", ["metrics", "date"])
def test_missing_required_field_litellm_result_keys(what, make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))

    def edit(body):
        for result in body.get("results") or []:
            result.pop(what, None)

    rc = _run(proxy, _rewriting(proxy, edit), fake_clock, data_dir)
    err = capsys.readouterr().err
    assert rc == 2
    assert what in err
    assert _no_records(data_dir)


def test_missing_required_field_litellm_user_list_user_id(make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))

    def handler(request: httpx.Request) -> httpx.Response:
        response = _orig(request)
        if response.status_code == 200 and request.url.path.endswith("/user/list"):
            body = json.loads(response.read().decode("utf-8"))
            for user in body["users"]:
                user.pop("user_id", None)
            return httpx.Response(200, json=body)
        return response

    _orig = proxy.transport.handle_request
    proxy.transport = httpx.MockTransport(handler)
    rc = backfill(data_dir, proxy, fake_clock)
    err = capsys.readouterr().err
    assert rc == 2
    assert "user_list" in err and "user_id" in err
    assert _no_records(data_dir)


def test_rebuild_over_a_damaged_raw_file_exits_2_and_leaves_records(make_proxy, fake_clock, data_dir, capsys):
    import gzip

    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    assert backfill(data_dir, proxy, fake_clock) == 0
    before = {str(p.relative_to(data_dir)): p.read_bytes() for p in (data_dir / "records").rglob("*.jsonl")}
    for raw in sorted((data_dir / "raw" / "user_daily_activity").rglob("*.json.gz")):
        envelope = json.loads(gzip.decompress(raw.read_bytes()).decode("utf-8"))
        for page in envelope["pages"]:
            body = json.loads(page["body"])
            for metrics in _all_metrics(body):
                metrics["spend"] = None
            page["body"] = json.dumps(body)
        raw.write_bytes(gzip.compress(json.dumps(envelope).encode("utf-8")))
    capsys.readouterr()

    from meterline_litellm.cli import main
    assert main(["rebuild", "--data-dir", str(data_dir)]) == 2
    err = capsys.readouterr().err
    assert "rebuild:" in err and "20260920T060000Z" in err and "spend" in err
    assert "Traceback" not in err and len(err.strip().splitlines()) == 1
    after = {str(p.relative_to(data_dir)): p.read_bytes() for p in (data_dir / "records").rglob("*.jsonl")}
    assert after == before
    assert not (data_dir / ".rebuild").exists()
