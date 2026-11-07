"""End-to-end: run the proxy against a fake Docker daemon on a unix socket."""

from __future__ import annotations

import json
import os
import socketserver
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.main import Upstream, make_handler
from app.validate_create import CreateRules
from tests.test_validate_create import good

CONTAINERS = {
    "movo1": {"Config": {"Labels": {"movo.project": "movo", "movo.role": "script-job"}}},
    "build1": {"Config": {"Labels": {"movo.project": "movo", "movo.role": "skill-dependency-build"}}},
    "foreign": {"Config": {"Labels": {}}},
}
NETWORKS = {"n1": {"Labels": {"movo.role": "sandbox-job-network"}}, "bridge": {"Labels": {}}}


class FakeDocker(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    calls: list = []

    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _any(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        FakeDocker.calls.append((self.command, self.path))
        path = self.path.split("?")[0]
        for cid, data in CONTAINERS.items():
            if path.endswith(f"/containers/{cid}/json"):
                return self._send(200, data)
        for nid, data in NETWORKS.items():
            if path.endswith(f"/networks/{nid}") and self.command == "GET":
                return self._send(200, data)
        if "/json" in path and ("/containers/" in path or "/exec/" in path):
            return self._send(404, {"message": "no such"})
        if path.startswith("/networks/") and self.command == "GET":
            return self._send(404, {"message": "no such"})
        return self._send(200 if self.command != "POST" or "create" not in path else 201, {"Id": "x"})

    do_GET = do_POST = do_DELETE = do_HEAD = _any


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        return request, ("local", 0)


@pytest.fixture()
def proxy():
    tmp = tempfile.mkdtemp()
    sock = os.path.join(tmp, "docker.sock")
    docker = UnixServer(sock, FakeDocker)
    threading.Thread(target=docker.serve_forever, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(CreateRules(), Upstream(sock)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeDocker.calls = []
    client = httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}/v1.45")
    yield client
    server.shutdown()
    docker.shutdown()


def test_allowed_create_is_forwarded(proxy) -> None:
    assert proxy.post("/containers/create", json=good()).status_code == 201
    assert any("create" in p for _, p in FakeDocker.calls)


def test_privileged_create_is_blocked_before_daemon(proxy) -> None:
    body = good()
    body["HostConfig"]["Privileged"] = True
    response = proxy.post("/containers/create", json=body)
    assert response.status_code == 403
    assert "Privileged" in response.json()["message"]
    assert not any("create" in p for _, p in FakeDocker.calls)


@pytest.mark.parametrize("method,path", [
    ("GET", "/containers/foreign/logs"), ("POST", "/containers/foreign/exec"),
    ("DELETE", "/containers/foreign"), ("POST", "/networks/bridge/connect"),
    ("POST", "/images/create"), ("POST", "/build"), ("GET", "/secrets"), ("POST", "/volumes/create"),
    ("POST", "/swarm/init"), ("POST", "/plugins/pull"), ("POST", "/containers/movo1/update"),
    ("POST", "/containers/movo1/attach"), ("PUT", "/containers/movo1/archive"),
    ("POST", "/commit?container=movo1&repo=movo-skill-deps&tag=x"),
    ("POST", "/commit?container=build1&repo=evil&tag=x"),
])
def test_disallowed_operations(proxy, method, path) -> None:
    assert proxy.request(method, path).status_code in {403, 404}


def test_labelled_container_operations_allowed(proxy) -> None:
    assert proxy.get("/containers/movo1/logs").status_code == 200
    assert proxy.post("/containers/movo1/start").status_code == 200
    assert proxy.post("/commit?container=build1&repo=movo-skill-deps&tag=abc").status_code == 200


def test_network_create_must_be_internal(proxy) -> None:
    assert proxy.post("/networks/create", json={"Name": "movo-x", "Driver": "bridge", "Internal": False,
                                                "Labels": {"movo.role": "r"}}).status_code == 403
    assert proxy.post("/networks/create", json={"Name": "movo-x", "Driver": "bridge", "Internal": True,
                                                "CheckDuplicate": True, "Labels": {"movo.role": "r"}}).status_code == 201
    assert proxy.post("/networks/n1/connect", json={"Container": "egress"}).status_code == 200
