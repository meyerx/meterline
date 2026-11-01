"""MF-1: a page the Admin API sends twice, or one it leaves out, must stop the run before any day is summed.

The fake pages by day buckets (``force_limit``). The transport below answers one page with the day buckets of another
while keeping the paging fields of the page that was asked for, so the cursor chain looks healthy: only the content
shows the fault.
"""

import json
from datetime import date, timedelta

import httpx

from admin_helpers import backfill, no_records
from meterline_anthropic_admin.testing import USAGE_PATH, crow, urow

PAGE = 4  # buckets per page: the 19 days 2026-09-01..2026-09-19 come as 5 pages


def _rows():
    usage, cost = [], []
    for n in range(19):
        day = (date(2026, 9, 1) + timedelta(days=n)).isoformat()
        usage.append(urow(day, "apikey_a", "model-a", uncached=100 + n, out=10 + n))
        cost.append(crow(day, "model-a", "uncached_input_tokens", "10"))
    return usage, cost


def _without_page(url):
    return url.copy_with(params=[(k, v) for k, v in url.params.multi_items() if k != "page"])


class _Swapped:
    """Stands in for a ``FakeAPI`` (the helpers read ``.transport``): ``asked`` is answered with the buckets of
    ``donor`` (``None`` is page 1) and the paging fields of ``asked``."""

    def __init__(self, api, asked, donor):
        def handler(request):
            real = api._handle(request)
            if request.url.path != USAGE_PATH or request.url.params.get("page") != asked:
                return real
            url = _without_page(request.url)
            if donor is not None:
                url = url.copy_add_param("page", donor)
            body = json.loads(real.text)
            body["data"] = json.loads(api._handle(httpx.Request("GET", url, headers=request.headers)).text)["data"]
            return httpx.Response(200, json=body)

        self.transport = httpx.MockTransport(handler)


def test_replayed_page_admin_is_refused_before_summing(make_api, fake_clock, data_dir, capsys):
    usage, cost = _rows()
    api = make_api(usage, cost, force_limit=PAGE)
    # the last page (days 17-19) is answered with the first page's buckets (days 1-4): days 1-4 twice, 17-19 gone
    code = backfill(data_dir, _Swapped(api, "page_16", None), fake_clock, since="2026-09-01")
    err = capsys.readouterr().err
    assert code == 2, err
    assert no_records(data_dir)
    assert "nothing published" in err


def test_replayed_page_admin_middle_page_is_refused(make_api, fake_clock, data_dir, capsys):
    usage, cost = _rows()
    api = make_api(usage, cost, force_limit=PAGE)
    # page 3 (days 9-12) is answered with page 2's buckets (days 5-8): days 5-8 twice, 9-12 gone
    code = backfill(data_dir, _Swapped(api, "page_8", "page_4"), fake_clock, since="2026-09-01")
    assert code == 2, capsys.readouterr().err
    assert no_records(data_dir)


def test_skipped_page_admin_is_refused(make_api, fake_clock, data_dir, capsys):
    usage, cost = _rows()
    api = make_api(usage, cost, force_limit=PAGE)
    # page 2 (days 5-8) is answered with page 3's buckets (days 9-12): days 5-8 never arrive
    code = backfill(data_dir, _Swapped(api, "page_4", "page_8"), fake_clock, since="2026-09-01")
    assert code == 2, capsys.readouterr().err
    assert no_records(data_dir)


def test_whole_window_in_one_page_still_publishes(make_api, fake_clock, data_dir, capsys):
    usage, cost = _rows()
    api = make_api(usage, cost)
    assert backfill(data_dir, api, fake_clock, since="2026-09-01") == 0, capsys.readouterr().err
    assert not no_records(data_dir)
