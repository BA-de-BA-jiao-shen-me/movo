"""sock-proxy: the only service that mounts docker.sock.

Listens on the backend network (default :2375) and forwards an allowlisted
subset of the Docker Engine API. Container creation bodies are validated
against ``validate_create``; operations on existing objects are only allowed
when the object carries the movo labels. Denials return 403 JSON and are
written to the audit log.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .rules import match
from .validate_create import CreateRules, Rejected, validate_create

LOG = logging.getLogger("sock-proxy")
MAX_BODY = 1024 * 1024
SKILL_REPO = "movo-skill-deps"


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 600) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


class Upstream:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        conn = UnixHTTPConnection(self.socket_path)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.getheader("Content-Type", "application/json"), response.read()
        finally:
            conn.close()

    def labels(self, kind: str, object_id: str) -> dict | None:
        path = {"container": f"/containers/{object_id}/json", "network": f"/networks/{object_id}",
                "exec": f"/exec/{object_id}/json"}[kind]
        status, _, raw = self.request("GET", path)
        if status != 200:
            return None
        data = json.loads(raw or b"{}")
        if kind == "exec":
            container = data.get("ContainerID")
            return self.labels("container", container) if container else None
        if kind == "container":
            return (data.get("Config") or {}).get("Labels") or {}
        return data.get("Labels") or {}


def audit(event: str, **fields) -> None:
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **fields}
    line = json.dumps(record, sort_keys=True)
    path = os.environ.get("MOVO_SOCK_PROXY_AUDIT_LOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass
    print(line, file=sys.stderr, flush=True)


class Decision(Exception):
    def __init__(self, reason: str, status: int = 403) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def authorize(method: str, raw_path: str, body: bytes, rules: CreateRules, upstream: Upstream) -> None:
    """Raise Decision when the request must not reach the Docker daemon."""
    parts = urlsplit(raw_path)
    found = match(method, parts.path)
    if found is None:
        raise Decision(f"{method} {parts.path} is not allowed")
    route, params = found
    query = parse_qs(parts.query)
    if route.kind == "create":
        try:
            validate_create(json.loads(body or b"null"), rules)
        except (ValueError, Rejected) as exc:
            raise Decision(f"container create rejected: {exc}") from exc
    elif route.kind == "network_create":
        try:
            data = json.loads(body or b"null")
        except ValueError as exc:
            raise Decision("invalid JSON") from exc
        if not isinstance(data, dict) or set(data) - {"Name", "Driver", "Internal", "CheckDuplicate", "Labels"}:
            raise Decision("unknown network fields")
        if data.get("Internal") is not True or data.get("Driver", "bridge") != "bridge":
            raise Decision("only internal bridge networks may be created")
        if not str(data.get("Name", "")).startswith("movo-") or not (data.get("Labels") or {}).get("movo.role"):
            raise Decision("networks must be movo-* and labelled")
    elif route.kind in {"container", "exec"}:
        labels = upstream.labels(route.kind, params["id"])
        if labels is None:
            if method == "DELETE":
                return  # idempotent delete of a missing object
            raise Decision("object not found", 404)
        if labels.get("movo.project") != rules.project:
            raise Decision("object is not managed by movo")
    elif route.kind == "network":
        labels = upstream.labels("network", params["id"])
        if labels is None:
            if method == "DELETE":
                return
            raise Decision("network not found", 404)
        if not labels.get("movo.role"):
            raise Decision("network is not managed by movo")
    elif route.kind == "commit":
        if (query.get("repo") or [""])[0] != SKILL_REPO:
            raise Decision("commit only allowed into the skill dependency repository")
        container = (query.get("container") or [""])[0]
        labels = upstream.labels("container", container) if container else None
        if not labels or labels.get("movo.role") != "skill-dependency-build" or labels.get("movo.project") != rules.project:
            raise Decision("commit only allowed from skill dependency build containers")
        if body:
            raise Decision("commit config overrides are not allowed")


def make_handler(rules: CreateRules, upstream: Upstream):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # noqa: D401 - quiet default logging
            LOG.debug(fmt, *args)

        def _reply(self, status: int, ctype: str, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY or self.headers.get("Transfer-Encoding"):
                self._reply(413, "application/json", b'{"message":"body too large or chunked"}')
                return
            body = self.rfile.read(length) if length else b""
            try:
                authorize(self.command, self.path, body, rules, upstream)
            except Decision as exc:
                audit("sock.denied", method=self.command, path=urlsplit(self.path).path, reason=exc.reason)
                self._reply(exc.status, "application/json", json.dumps({"message": exc.reason}).encode())
                return
            headers = {"Content-Type": self.headers.get("Content-Type", "application/json")} if body else {}
            status, ctype, payload = upstream.request(self.command, self.path, body or None, headers)
            if self.command in {"POST", "DELETE"}:
                audit("sock.allowed", method=self.command, path=urlsplit(self.path).path, status=status)
            self._reply(status, ctype, payload)

        do_GET = do_POST = do_DELETE = do_HEAD = _handle

        def do_PUT(self):  # noqa: N802
            self._reply(403, "application/json", b'{"message":"method not allowed"}')

        do_PATCH = do_PUT

    return Handler


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    rules = CreateRules.from_env()
    upstream = Upstream(os.environ.get("MOVO_DOCKER_SOCKET", "/var/run/docker.sock"))
    port = int(os.environ.get("MOVO_SOCK_PROXY_PORT", "2375"))
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(rules, upstream))
    audit("sock.start", runtime=rules.runtime, tenant_mode=rules.tenant_mode)
    server.serve_forever()


if __name__ == "__main__":
    main()
