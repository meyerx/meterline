"""F-31: a test that opens a connection to a non-loopback address fails at once.
Loopback (127.0.0.0/8, ::1, localhost) and AF_UNIX stay open for local fakes."""

import ipaddress
import socket

import pytest


def _is_local(sock, address):
    if sock.family == getattr(socket, "AF_UNIX", None):
        return True
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host).split("%")[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_local_name(host):
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host).split("%")[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def refuse(sock, address):
        pytest.fail(f"test tried to reach the network: connect to {address!r} "
                    "(only loopback and unix sockets are allowed)", pytrace=False)

    def connect(self, address):
        if not _is_local(self, address):
            refuse(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        if not _is_local(self, address):
            refuse(self, address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)

    real_getaddrinfo = socket.getaddrinfo
    real_gethostbyname, real_gethostbyname_ex = socket.gethostbyname, socket.gethostbyname_ex

    def refuse_lookup(host):
        pytest.fail(f"test tried to reach the network: resolve {host!r} "
                    "(only loopback names and addresses are allowed)", pytrace=False)

    def getaddrinfo(host, *args, **kwargs):
        if not _is_local_name(host):
            refuse_lookup(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def gethostbyname(host):
        if not _is_local_name(host):
            refuse_lookup(host)
        return real_gethostbyname(host)

    def gethostbyname_ex(host):
        if not _is_local_name(host):
            refuse_lookup(host)
        return real_gethostbyname_ex(host)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyname", gethostbyname)
    monkeypatch.setattr(socket, "gethostbyname_ex", gethostbyname_ex)
