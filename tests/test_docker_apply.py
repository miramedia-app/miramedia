"""Tests for perform_docker_apply using httpx MockTransport (no real Docker socket)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from miramedia.updates import docker_apply
from miramedia.updates.schemas import UpdateStatusState

_IMAGE_REPO = "ghcr.io/org/miramedia"
_IMAGE_TAG = "latest"
_CONTAINER_NAME = "miramedia"
_SOCKET_PATH = "/var/run/docker.sock"


def _make_transport(
    spec: dict[str, Any],
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)

        if request.method == "POST" and request.url.path == "/images/create":
            status = spec.get("pull_status", 200)
            if status >= 400:
                body = spec.get("pull_error_body", b"internal error")
                return httpx.Response(status, content=body)
            body = spec.get(
                "pull_body",
                b'{"status":"Pulling from x","progress":""}\n'
                b'{"status":"Download complete"}\n',
            )
            return httpx.Response(200, content=body)

        if request.method == "GET" and request.url.path.startswith("/images/"):
            inspect = spec.get("inspect_image", {"Id": "sha256:new"})
            if inspect == "404":
                return httpx.Response(404)
            return httpx.Response(200, json=inspect)

        if request.method == "GET" and request.url.path == "/containers/json":
            return httpx.Response(200, json=spec.get("containers", []))

        if request.method == "POST" and "/stop" in request.url.path:
            stop_status = spec.get("stop_status", 204)
            if stop_status not in (204, 304):
                return httpx.Response(stop_status, text="stop failed")
            return httpx.Response(stop_status)

        return httpx.Response(404, text=f"unhandled {request.method} {request.url}")

    return httpx.MockTransport(handler), requests


def _run_apply(
    monkeypatch: pytest.MonkeyPatch,
    spec: dict[str, Any],
) -> tuple[list[str], list[UpdateStatusState], list[httpx.Request]]:
    transport, requests = _make_transport(spec)
    logs: list[str] = []
    states: list[UpdateStatusState] = []

    def fake_client(_socket_path: str) -> httpx.Client:
        return httpx.Client(transport=transport, base_url="http://docker")

    monkeypatch.setattr(docker_apply, "_client", fake_client)

    docker_apply.perform_docker_apply(
        socket_path=_SOCKET_PATH,
        image_repository=_IMAGE_REPO,
        image_tag=_IMAGE_TAG,
        container_name=_CONTAINER_NAME,
        on_log=logs.append,
        on_state=states.append,
    )
    return logs, states, requests


def _stop_requests(requests: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in requests if r.method == "POST" and "/stop" in r.url.path]


def test_happy_path_pull_inspect_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs, states, requests = _run_apply(
        monkeypatch,
        {
            "pull_body": (
                b'{"status":"Pulling from x","progress":""}\n'
                b'{"status":"Download complete"}\n'
            ),
            "inspect_image": {"Id": "sha256:new"},
            "containers": [
                {
                    "Id": "c1",
                    "Names": ["/miramedia"],
                    "ImageID": "sha256:old",
                }
            ],
            "stop_status": 204,
        },
    )

    assert states == [UpdateStatusState.pulling, UpdateStatusState.restarting]
    stop = _stop_requests(requests)
    assert len(stop) == 1
    assert stop[0].url.path == "/containers/c1/stop"
    assert "Pulling from x" in logs
    assert "Download complete" in logs


def test_already_latest_skips_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    logs, states, requests = _run_apply(
        monkeypatch,
        {
            "inspect_image": {"Id": "sha256:same"},
            "containers": [
                {
                    "Id": "c1",
                    "Names": ["/miramedia"],
                    "ImageID": "sha256:same",
                }
            ],
        },
    )

    assert states == [UpdateStatusState.pulling]
    assert _stop_requests(requests) == []
    assert any("already running latest image" in line for line in logs)


def test_pull_error_line_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="docker pull error"):
        _run_apply(
            monkeypatch,
            {
                "pull_body": b'{"error":"manifest unknown"}\n',
            },
        )


def test_pull_error_line_no_container_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport, requests = _make_transport(
        {"pull_body": b'{"error":"manifest unknown"}\n'},
    )

    def fake_client(_socket_path: str) -> httpx.Client:
        return httpx.Client(transport=transport, base_url="http://docker")

    monkeypatch.setattr(docker_apply, "_client", fake_client)

    with pytest.raises(RuntimeError, match="docker pull error"):
        docker_apply.perform_docker_apply(
            socket_path=_SOCKET_PATH,
            image_repository=_IMAGE_REPO,
            image_tag=_IMAGE_TAG,
            container_name=_CONTAINER_NAME,
            on_log=lambda _: None,
            on_state=lambda _: None,
        )

    assert not any(r.url.path == "/containers/json" for r in requests)
    assert not _stop_requests(requests)


def test_pull_http_500_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match=r"docker pull failed \(500\)"):
        _run_apply(monkeypatch, {"pull_status": 500})


def test_container_not_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match=r"updates\.container_name"):
        _run_apply(
            monkeypatch,
            {
                "inspect_image": {"Id": "sha256:new"},
                "containers": [],
            },
        )


def test_container_name_match_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match=r"updates\.container_name"):
        _run_apply(
            monkeypatch,
            {
                "inspect_image": {"Id": "sha256:new"},
                "containers": [{"Id": "c2", "Names": ["/miramedia-web"]}],
            },
        )


def test_stop_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="docker stop failed"):
        _run_apply(
            monkeypatch,
            {
                "inspect_image": {"Id": "sha256:new"},
                "containers": [
                    {
                        "Id": "c1",
                        "Names": ["/miramedia"],
                        "ImageID": "sha256:old",
                    }
                ],
                "stop_status": 500,
            },
        )


def test_image_vanished_after_pull_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="not found"):
        _run_apply(monkeypatch, {"inspect_image": "404"})


def test_malformed_pull_json_line_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    logs, states, requests = _run_apply(
        monkeypatch,
        {
            "pull_body": (
                b'{"status":"Pulling from x"}\n'
                b"not-json\n"
                b'{"status":"Download complete"}\n'
            ),
            "inspect_image": {"Id": "sha256:new"},
            "containers": [
                {
                    "Id": "c1",
                    "Names": ["/miramedia"],
                    "ImageID": "sha256:old",
                }
            ],
        },
    )

    assert states == [UpdateStatusState.pulling, UpdateStatusState.restarting]
    assert "Pulling from x" in logs
    assert "Download complete" in logs
    assert len(_stop_requests(requests)) == 1
