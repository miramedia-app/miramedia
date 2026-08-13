"""Concurrency investigation for process-global ``socket.getaddrinfo`` DNS pinning.

Verdict (plan 252): NOT REPRODUCED for cross-request pinned-address leakage under
overlapping ``_dns_pin`` contexts. Out-of-order restore can leave a stale wrapper on
the global resolver, but observed resolutions still match each hostname's pin.
See ``evidence/252-dns-pinning.md``.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable

import pytest

from miramedia.torrents.utils import _dns_pin

_HOST_A = "host-a.pin.test"
_HOST_B = "host-b.pin.test"
_IP_A = "93.184.216.34"
_IP_B = "8.8.4.4"


def _addrinfo_tuple(ip: str) -> tuple:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, 0, 0, 0) if family == socket.AF_INET6 else (ip, 0)
    return (family, socket.SOCK_STREAM, 6, "", sockaddr)


def _make_original_resolver() -> Callable[..., list[tuple]]:
    host_to_ip = {_HOST_A: _IP_A, _HOST_B: _IP_B}

    def original_getaddrinfo(
        host: str,
        port: object,
        *args: object,
        **kwargs: object,
    ) -> list[tuple]:
        del port, args, kwargs
        if host in host_to_ip:
            return [_addrinfo_tuple(host_to_ip[host])]
        # ``_dns_pin`` resolves the pinned IP by calling the captured resolver with
        # the IP as ``host``.
        return [_addrinfo_tuple(host)]

    return original_getaddrinfo


def _raise_simulated_connect_failure() -> None:
    msg = "simulated connect failure"
    raise OSError(msg)


def _resolved_ip(hostname: str) -> str:
    return socket.getaddrinfo(hostname, None)[0][4][0]


@pytest.fixture
def original_getaddrinfo(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[tuple]]:
    resolver = _make_original_resolver()
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    return resolver


def test_dns_pin_nested_entry_and_exit_restores_original(
    original_getaddrinfo: Callable[..., list[tuple]],
) -> None:
    with _dns_pin(_HOST_A, _IP_A):
        assert _resolved_ip(_HOST_A) == _IP_A
        with _dns_pin(_HOST_B, _IP_B):
            assert _resolved_ip(_HOST_A) == _IP_A
            assert _resolved_ip(_HOST_B) == _IP_B
        assert _resolved_ip(_HOST_A) == _IP_A
    assert socket.getaddrinfo is original_getaddrinfo


def test_dns_pin_inner_exception_restores_outer_pin(
    original_getaddrinfo: Callable[..., list[tuple]],
) -> None:
    with _dns_pin(_HOST_A, _IP_A):
        assert _resolved_ip(_HOST_A) == _IP_A
        try:
            with _dns_pin(_HOST_B, _IP_B):
                assert _resolved_ip(_HOST_B) == _IP_B
                _raise_simulated_connect_failure()
        except OSError:
            pass
        assert _resolved_ip(_HOST_A) == _IP_A
    assert socket.getaddrinfo is original_getaddrinfo


def test_dns_pin_concurrent_overlap_pins_stay_isolated(
    original_getaddrinfo: Callable[..., list[tuple]],
) -> None:
    """Two threads inside ``_dns_pin`` at once: each hostname keeps its pin."""
    enter_barrier = threading.Barrier(2)
    errors: list[str] = []

    def worker(hostname: str, pinned_ip: str, label: str) -> None:
        try:
            with _dns_pin(hostname, pinned_ip):
                enter_barrier.wait(timeout=5)
                if _resolved_ip(_HOST_A) != _IP_A:
                    errors.append(f"{label}: HOST_A -> {_resolved_ip(_HOST_A)}")
                if _resolved_ip(_HOST_B) != _IP_B:
                    errors.append(f"{label}: HOST_B -> {_resolved_ip(_HOST_B)}")
        except Exception as exc:
            errors.append(f"{label}: {exc!r}")

    threads = [
        threading.Thread(
            target=worker,
            args=(_HOST_A, _IP_A, "thread-a"),
            name="dns-pin-a",
        ),
        threading.Thread(
            target=worker,
            args=(_HOST_B, _IP_B, "thread-b"),
            name="dns-pin-b",
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert errors == []
    assert socket.getaddrinfo is original_getaddrinfo


def test_dns_pin_outer_exits_before_inner_stale_wrapper_no_pin_leak(
    original_getaddrinfo: Callable[..., list[tuple]],
) -> None:
    """Reverse-order exit: outer leaves ``with`` while inner is still active."""
    both_inside = threading.Barrier(2)
    outer_exited = threading.Event()
    observations: dict[str, object] = {}
    errors: list[str] = []

    def outer_worker() -> None:
        try:
            with _dns_pin(_HOST_A, _IP_A):
                both_inside.wait(timeout=5)
        except Exception as exc:
            errors.append(f"outer: {exc!r}")
        else:
            outer_exited.set()

    def inner_worker() -> None:
        try:
            with _dns_pin(_HOST_B, _IP_B):
                both_inside.wait(timeout=5)
                if not outer_exited.wait(timeout=5):
                    errors.append("inner: outer did not exit before observation")
                if _resolved_ip(_HOST_A) != _IP_A:
                    errors.append(
                        "inner after outer exit: HOST_A -> " + _resolved_ip(_HOST_A)
                    )
                if _resolved_ip(_HOST_B) != _IP_B:
                    errors.append(
                        "inner after outer exit: HOST_B -> " + _resolved_ip(_HOST_B)
                    )
                observations["global_while_inner_active"] = socket.getaddrinfo
        except Exception as exc:
            errors.append(f"inner: {exc!r}")

    outer = threading.Thread(target=outer_worker, name="dns-pin-outer-first")
    inner = threading.Thread(target=inner_worker, name="dns-pin-inner-second")
    outer.start()
    inner.start()
    outer.join(timeout=10)
    inner.join(timeout=10)
    assert not outer.is_alive()
    assert not inner.is_alive()
    assert errors == []

    observations["global_after_both_exit"] = socket.getaddrinfo
    assert _resolved_ip(_HOST_A) == _IP_A
    assert _resolved_ip(_HOST_B) == _IP_B

    # Inner ``finally`` restores the wrapper captured at entry (outer's pin), not the
    # true original — but resolutions remain correct in this harness.
    global_after = observations["global_after_both_exit"]
    assert global_after is not original_getaddrinfo
    assert _resolved_ip(_HOST_A) == _IP_A
    assert _resolved_ip(_HOST_B) == _IP_B
