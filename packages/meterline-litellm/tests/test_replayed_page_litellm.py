"""MF-1: a page the proxy sends twice, with its page number echoed correctly, must stop the run before any date is
summed (exit 2, nothing published). LiteLLM has no row identity, so a replay shows as the same date result, digit for
digit, on two pages; a date cut across pages carries different rows on each page and is not a replay."""

import json

import httpx

from litellm_helpers import backfill, basic_rows, manifests, users
from meterline_litellm.testing import PATH_A


class _Replaying:
    """Stands in for a ``FakeAPI``: the answer to ``asked`` carries the results of ``donor`` and its own metadata."""

    def __init__(self, proxy, asked, donor):
        def handler(request):
            real = proxy._handle(request)
            if not request.url.path.endswith(PATH_A) or request.url.params.get("page") != str(asked):
                return real
            params = dict(request.url.params)
            params["page"] = str(donor)
            other = httpx.Request("GET", request.url.copy_with(params=params), headers=request.headers)
            body = json.loads(real.text)
            body["results"] = json.loads(proxy._handle(other).text)["results"]
            return httpx.Response(200, json=body)

        self.transport = httpx.MockTransport(handler)


def _no_records(data_dir):
    folder = data_dir / "records"
    return not (folder.exists() and list(folder.rglob("*.jsonl")))


def test_replayed_page_litellm_is_refused_before_summing(make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"), page_cap=2)
    # page 2 (rest of 09-15 and 09-16) is answered with page 1's results, and page 2's own number
    code = backfill(data_dir, _Replaying(proxy, 2, 1), fake_clock, since="2026-09-15")
    err = capsys.readouterr().err
    assert code == 2, err
    assert _no_records(data_dir)
    assert "nothing published" in err
    assert manifests(data_dir)[0]["status"] == "failed"


def test_cut_date_across_pages_is_not_a_replay(make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"), page_cap=2)
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0, capsys.readouterr().err
    assert not _no_records(data_dir)
