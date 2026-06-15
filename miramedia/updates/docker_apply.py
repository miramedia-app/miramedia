"""Talk to the local Docker daemon over its UNIX socket to pull a new image and
trigger self-recreation. Avoids docker-py to keep the dependency footprint small.

Strategy:
1. Pull `<image_repository>:<image_tag>` via `POST /images/create`.
2. Find our own container by name and inspect its current image.
3. If the running image already matches the digest of the freshly pulled tag,
   skip the recreate (nothing to do).
4. Otherwise, kill our own container so docker compose's
   `restart: unless-stopped` policy recreates it from the (now updated) tag.

Caveats:
- Requires `/var/run/docker.sock` to be mounted into the container.
- Requires the running compose stack to use a stable container name and the
   `:latest` tag (or whatever `image_tag` resolves to).
- Recreate semantics rely on docker compose / restart policy. If the container
   was started with `--rm` or no restart policy, it will simply die.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx

from miramedia.updates.schemas import UpdateStatusState

log = logging.getLogger(__name__)


def _client(socket_path: str) -> httpx.Client:
    transport = httpx.HTTPTransport(uds=socket_path)
    return httpx.Client(
        transport=transport,
        base_url="http://docker",
        timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0),
    )


def _pull_image(
    client: httpx.Client,
    image_repository: str,
    image_tag: str,
    on_log: Callable[[str], None],
) -> None:
    params = {"fromImage": image_repository, "tag": image_tag}
    url = "/images/create?" + urllib.parse.urlencode(params)
    with client.stream("POST", url) as resp:
        if resp.status_code >= 400:
            body = resp.read().decode("utf-8", errors="replace")
            msg = f"docker pull failed ({resp.status_code}): {body}"
            raise RuntimeError(msg)
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if "error" in payload:
                msg = f"docker pull error: {payload['error']}"
                raise RuntimeError(msg)
            status = payload.get("status")
            if status:
                progress = payload.get("progress") or ""
                on_log(f"{status} {progress}".strip())


def _find_container(client: httpx.Client, name: str) -> dict[str, Any] | None:
    filters = json.dumps({"name": [name]})
    resp = client.get(
        "/containers/json",
        params={"all": "true", "filters": filters},
    )
    resp.raise_for_status()
    for entry in resp.json():
        names = entry.get("Names") or []
        if any(n.lstrip("/") == name for n in names):
            return entry
    return None


def _inspect_image(client: httpx.Client, ref: str) -> dict[str, Any] | None:
    resp = client.get(f"/images/{urllib.parse.quote(ref, safe='')}/json")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _kill_container(client: httpx.Client, container_id: str) -> None:
    # SIGTERM first; docker recreates via restart policy.
    resp = client.post(f"/containers/{container_id}/stop", params={"t": 10})
    if resp.status_code not in (204, 304):
        body = resp.text
        msg = f"docker stop failed ({resp.status_code}): {body}"
        raise RuntimeError(msg)


def perform_docker_apply(
    *,
    socket_path: str,
    image_repository: str,
    image_tag: str,
    container_name: str,
    on_log: Callable[[str], None],
    on_state: Callable[[UpdateStatusState], None],
) -> None:
    on_state(UpdateStatusState.pulling)
    on_log(f"connecting to docker socket {socket_path}")

    with _client(socket_path) as client:
        # Pull the new image
        _pull_image(client, image_repository, image_tag, on_log)
        on_log(f"pulled {image_repository}:{image_tag}")

        # Compare digests so we don't bounce the container needlessly
        new_image = _inspect_image(client, f"{image_repository}:{image_tag}")
        if new_image is None:
            msg = f"freshly pulled image {image_repository}:{image_tag} not found"
            raise RuntimeError(msg)

        container = _find_container(client, container_name)
        if container is None:
            msg = (
                f"container {container_name!r} not found — check updates.container_name"
            )
            raise RuntimeError(msg)
        current_image_id = container.get("ImageID") or container.get("Image")
        new_image_id = new_image.get("Id")
        if current_image_id and new_image_id and current_image_id == new_image_id:
            on_log("already running latest image — nothing to do")
            return

        on_state(UpdateStatusState.restarting)
        on_log(
            f"stopping container {container_name} so restart policy recreates it "
            f"with new image ({new_image_id})"
        )
        _kill_container(client, container["Id"])
