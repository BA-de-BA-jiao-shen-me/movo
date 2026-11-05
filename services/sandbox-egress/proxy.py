"""HTTP CONNECT proxy that only dials globally routable addresses.

Job containers have an internal-only Docker network and reach this proxy by
name. The proxy is the sole network egress point for those containers.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import sys
import time
from urllib.parse import urlsplit


MAX_HEADER = 16 * 1024
MAX_CONNECTIONS = 256
_slots = asyncio.Semaphore(MAX_CONNECTIONS)

# P0 egress policy. Default deny; package registries needed by sandbox builds
# are allowed out of the box. Extra domains come from MOVO_EGRESS_ALLOW_DOMAINS
# (comma separated) or the policy file written by admin-api.
DEFAULT_ALLOWED = (
    "pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "registry.npmmirror.com",
    "deb.debian.org", "security.debian.org", "mirrors.aliyun.com", "pypi.tuna.tsinghua.edu.cn",
    "mirrors.tuna.tsinghua.edu.cn",
)
POLICY_FILE = os.environ.get("MOVO_EGRESS_POLICY_FILE", "/state/egress-policy.json")


def load_policy(env=None, policy_file: str | None = None) -> tuple[str, frozenset[str]]:
    env = os.environ if env is None else env
    mode = (env.get("MOVO_EGRESS_DEFAULT_POLICY") or "deny").strip().lower()
    if mode not in {"allow", "deny"}:
        raise SystemExit(f"invalid MOVO_EGRESS_DEFAULT_POLICY={mode!r}")
    domains = set(DEFAULT_ALLOWED)
    domains.update(d.strip().lower() for d in (env.get("MOVO_EGRESS_ALLOW_DOMAINS") or "").split(",") if d.strip())
    path = policy_file or POLICY_FILE
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        domains.update(str(d).strip().lower() for d in data.get("allow_domains", []) if str(d).strip())
    except (OSError, ValueError, AttributeError):
        pass
    return mode, frozenset(domains)


def host_allowed(host: str, mode: str, domains: frozenset[str]) -> bool:
    if mode == "allow":
        return True
    host = host.strip("[]").rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
        return False  # raw IPs bypass domain policy; refuse under deny
    except ValueError:
        pass
    return any(host == d or host.endswith("." + d) for d in domains)


def audit(event: str, **fields) -> None:
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **fields}
    print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)


_MODE, _DOMAINS = load_policy()


def _check_policy(host: str) -> None:
    if not host_allowed(host, _MODE, _DOMAINS):
        audit("egress.denied", host=host, reason="not in allowlist")
        raise PermissionError("Destination is not allowlisted")


async def _public_destination(host: str, port: int) -> tuple[str, int]:
    if not host or not 1 <= port <= 65535:
        raise ValueError("Invalid destination")
    records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for _, _, _, _, address in records:
        ip = ipaddress.ip_address(address[0])
        if ip.is_global:
            return str(ip), port
    raise PermissionError("Destination is not public")


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.write_eof()
        except (OSError, RuntimeError):
            pass


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    async with _slots:
        upstream = None
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
            if len(header) > MAX_HEADER:
                raise ValueError("Request headers are too large")
            lines = header.split(b"\r\n")
            method, target, _ = lines[0].decode("ascii").split(" ", 2)
            if method == "CONNECT":
                host, separator, port_text = target.rpartition(":")
                if not separator or not port_text.isdigit():
                    raise ValueError("Invalid CONNECT target")
                _check_policy(host)
                address = await _public_destination(host.strip("[]"), int(port_text))
                upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_connection(*address), timeout=15)
                upstream = upstream_writer
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            elif method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                url = urlsplit(target)
                if url.scheme != "http" or not url.hostname or url.username or url.password:
                    raise ValueError("Only plain HTTP absolute URLs are accepted")
                _check_policy(url.hostname)
                address = await _public_destination(url.hostname, url.port or 80)
                upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_connection(*address), timeout=15)
                upstream = upstream_writer
                path = (url.path or "/") + (f"?{url.query}" if url.query else "")
                upstream_writer.write(f"{method} {path} HTTP/1.1\r\n".encode("ascii") + b"\r\n".join(lines[1:]))
            else:
                raise ValueError("Unsupported proxy method")
            await upstream_writer.drain()
            await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))
        except (ValueError, PermissionError, asyncio.TimeoutError, asyncio.LimitOverrunError, OSError):
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        finally:
            if upstream is not None:
                upstream.close()
            writer.close()
            await writer.wait_closed()


async def main() -> None:
    server = await asyncio.start_server(_handle, "0.0.0.0", 3128, limit=MAX_HEADER)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
