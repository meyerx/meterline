"""A14 (spec section 8): the network guard of packages/conftest.py holds, so no test of this package touches the network.
A test that tries fails at once; the connector and the calibration script only ever get a mock transport."""

import socket
from pathlib import Path

import pytest

from admin_helpers import BASE_URL, FAKE_KEY
from meterline_anthropic_admin.client import AdminClient

GUARD = Path(__file__).resolve().parents[2] / "conftest.py"


def test_done_check_A14_the_guard_file_exists_next_to_the_packages():
    assert GUARD.is_file() and "_no_network" in GUARD.read_text(encoding="utf-8")


def test_done_check_A14_a_connection_to_a_public_address_fails_the_test():
    with pytest.raises(pytest.fail.Exception, match="reach the network"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("203.0.113.7", 443))


def test_done_check_A14_resolving_the_anthropic_host_fails_the_test():
    with pytest.raises(pytest.fail.Exception, match="reach the network"):
        socket.getaddrinfo("api.anthropic.com", 443)


def test_done_check_A14_a_client_without_a_mock_transport_cannot_reach_the_api():
    with pytest.raises(pytest.fail.Exception, match="reach the network"), AdminClient(FAKE_KEY, base_url=BASE_URL) as client:
        client.get_page("/v1/organizations/cost_report", {"starting_at": "2026-09-15T00:00:00Z"})


def test_done_check_A14_loopback_stays_open_for_local_fakes():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket()
    client.connect(server.getsockname())
    client.close()
    server.close()


def test_done_check_A14_no_test_builds_a_client_without_a_transport():
    offenders = []
    for path in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "AdminClient(" in line and "transport" not in line and "def " not in line:
                offenders.append("%s:%d" % (path.name, number))
    assert offenders == []
