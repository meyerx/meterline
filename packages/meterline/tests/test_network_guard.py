"""F-31: the test run never reaches a real network. packages/conftest.py makes a
connect to a non-loopback address fail at once with the guard's error."""

import socket

import pytest


def test_non_loopback_connect_is_blocked_before_any_network_use():
    # 192.0.2.1 is TEST-NET-1: never routable, so even a guard failure cannot reach anything
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        with pytest.raises(pytest.fail.Exception, match=r"tried to reach the network.*192\.0\.2\.1"):
            s.connect(("192.0.2.1", 9))


def test_non_loopback_create_connection_is_blocked():
    with pytest.raises(pytest.fail.Exception, match=r"tried to reach the network.*192\.0\.2\.1"):
        socket.create_connection(("192.0.2.1", 9), timeout=0.2)


def test_loopback_connect_is_not_blocked():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(server.getsockname())


@pytest.mark.parametrize("lookup", [
    lambda host: socket.getaddrinfo(host, 443),
    lambda host: socket.gethostbyname(host),
    lambda host: socket.gethostbyname_ex(host),
], ids=["getaddrinfo", "gethostbyname", "gethostbyname_ex"])
def test_name_lookup_of_an_outside_host_is_blocked(lookup):
    with pytest.raises(pytest.fail.Exception, match=r"tried to reach the network: resolve 'example\.invalid'"):
        lookup("example.invalid")


def test_name_lookup_of_loopback_is_not_blocked():
    assert socket.getaddrinfo("localhost", 80)
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo(None, 80)
    assert socket.gethostbyname("127.0.0.1") == "127.0.0.1"
