"""F-41: a page or file list is complete or the run fails. A seat page that
repeats, a seat page that goes missing, a download link listed twice, a file
that comes back twice, or more pages than the cap stop the run with exit 2
(a failed manifest, nothing published) instead of publishing fewer people.
"""

import json

import httpx
import pytest

from meterline_copilot.testing import seat, user_row
from copilot_helpers import FAKE_BILLING_TOKEN, FAKE_TOKEN, backfill, manifests, records
from meterline_copilot import fetch as fetch_module
from meterline_copilot.client import CopilotClient
from meterline_copilot.fetch import Level, PaginationError, fetch_report, fetch_seats

DAY = "2026-09-18"


def _client(transport, clock):
    return CopilotClient(FAKE_TOKEN, billing_token=FAKE_BILLING_TOKEN, transport=transport, clock=clock,
                         sleep=clock.sleep, wall=clock, jitter=lambda: 0, rate_per_hour=100000)


def _seat_pages(pages, total, next_link=True):
    """A transport serving the given seat lists as consecutive pages."""
    calls = []

    def handler(request):
        number = int(request.url.params.get("page", "1"))
        calls.append(number)
        if len(calls) > 100:
            raise AssertionError("the loop did not stop")
        chunk = pages(number)
        headers = {}
        if next_link and chunk is not None and chunk[1]:
            headers["link"] = '<https://api.github.com/orgs/example-org/copilot/billing/seats?page=%d>; rel="next"' % (number + 1)
        return httpx.Response(200, json={"total_seats": total, "seats": chunk[0]}, headers=headers)

    return httpx.MockTransport(handler), calls


def test_cursor_repeat_seat_page_that_repeats_stops(fake_clock, tmp_path):
    transport, calls = _seat_pages(lambda n: ([seat("login1", 1)], True), total=2)
    with pytest.raises(PaginationError, match="page 2 is the same as page 1"):
        fetch_seats(_client(transport, fake_clock), tmp_path, Level("organization", "example-org"),
                    __import__("datetime").datetime(2026, 9, 20, 6, tzinfo=__import__("datetime").timezone.utc))
    assert calls == [1, 2]


def test_cursor_repeat_seat_page_that_goes_missing_stops(fake_clock, tmp_path):
    from datetime import datetime, timezone

    transport, _calls = _seat_pages(lambda n: ([seat("login1", 1)], True) if n == 1 else ([seat("login3", 3)], False), total=3)
    with pytest.raises(PaginationError, match="2 distinct people but total_seats says 3"):
        fetch_seats(_client(transport, fake_clock), tmp_path, Level("organization", "example-org"),
                    datetime(2026, 9, 20, 6, tzinfo=timezone.utc))
    assert not list(tmp_path.rglob("*.json.gz")), "nothing is stored for a query that does not add up"


def test_cursor_repeat_endless_distinct_seat_pages_hit_the_page_cap(fake_clock, tmp_path, monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(fetch_module, "MAX_PAGES", 4)
    transport, calls = _seat_pages(lambda n: ([seat("login%d" % n, n)], True), total=100)
    with pytest.raises(PaginationError, match="more than 4 pages"):
        fetch_seats(_client(transport, fake_clock), tmp_path, Level("organization", "example-org"),
                    datetime(2026, 9, 20, 6, tzinfo=timezone.utc))
    assert len(calls) == 4


def test_cursor_repeat_seats_that_add_up_are_untouched(fake_clock, tmp_path):
    from datetime import datetime, timezone

    transport, _calls = _seat_pages(lambda n: ([seat("login%d" % n, n)], n < 3), total=3)
    pull = fetch_seats(_client(transport, fake_clock), tmp_path, Level("organization", "example-org"),
                       datetime(2026, 9, 20, 6, tzinfo=timezone.utc))
    assert pull.pages == 3


def _report_transport(links, files):
    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"download_links": links})
        return httpx.Response(200, text=files[links.index(str(request.url))])

    return httpx.MockTransport(handler)


def test_cursor_repeat_a_download_link_listed_twice_stops(fake_clock, tmp_path):
    from datetime import datetime, timezone

    link = "https://downloads.example.com/users-%s-1.ndjson?sig=a" % DAY
    transport = _report_transport([link, link], ["{}\n", "{}\n"])
    with pytest.raises(PaginationError, match="listed twice"):
        fetch_report(_client(transport, fake_clock), tmp_path, Level("organization", "example-org"), "users",
                     __import__("datetime").date(2026, 9, 18), datetime(2026, 9, 20, 6, tzinfo=timezone.utc))


def test_cursor_repeat_a_file_that_comes_back_twice_stops(fake_clock, tmp_path):
    from datetime import date, datetime, timezone

    links = ["https://downloads.example.com/users-%s-%d.ndjson?sig=a" % (DAY, i) for i in (1, 2)]
    row = json.dumps({"user_id": 1, "day": DAY}) + "\n"
    transport = _report_transport(links, [row, row])
    with pytest.raises(PaginationError, match="same content"):
        fetch_report(_client(transport, fake_clock), tmp_path, Level("organization", "example-org"), "users",
                     date(2026, 9, 18), datetime(2026, 9, 20, 6, tzinfo=timezone.utc))


def test_cursor_repeat_ends_a_command_with_exit_2(make_api, fake_clock, data_dir, level, capsys):
    api = make_api(seats_per_page=1)
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level)]
    api.seats = [seat("login1", 1), seat("login2", 2), seat("login3", 3)]
    inner = api.transport

    def handler(request):
        response = inner.handle_request(request)
        if request.url.path.endswith("/copilot/billing/seats") and request.url.params.get("page") == "2":
            return inner.handle_request(httpx.Request("GET", request.url.copy_with(params={"page": "3", "per_page": "1"}),
                                                      headers=request.headers))
        return response

    api.transport = httpx.MockTransport(handler)
    rc = backfill(data_dir, api, fake_clock, since=DAY)
    err = capsys.readouterr().err
    assert rc == 2
    assert "total_seats" in err and "Traceback" not in err
    assert manifests(data_dir)[-1]["status"] == "failed"
    assert records(data_dir, "seat_assignments") == []
