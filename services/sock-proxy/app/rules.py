"""Docker Engine API path/method allowlist."""

from __future__ import annotations

import re
from dataclasses import dataclass

VERSION = r"(?:/v1\.\d+)?"
ID = r"[A-Za-z0-9_.-]{1,128}"


@dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern
    kind: str  # plain | create | container | network_create | network | exec | commit


def _r(method: str, path: str, kind: str = "plain") -> Route:
    return Route(method, re.compile(f"^{VERSION}{path}$"), kind)


ROUTES = (
    _r("GET", r"/_ping"), _r("HEAD", r"/_ping"), _r("GET", r"/version"), _r("GET", r"/info"),
    _r("GET", r"/containers/json"),
    _r("POST", r"/containers/create", "create"),
    _r("POST", rf"/containers/(?P<id>{ID})/(?:start|wait|kill|exec)", "container"),
    _r("GET", rf"/containers/(?P<id>{ID})/(?:json|logs)", "container"),
    _r("DELETE", rf"/containers/(?P<id>{ID})", "container"),
    _r("POST", rf"/exec/(?P<id>{ID})/start", "exec"),
    _r("GET", rf"/exec/(?P<id>{ID})/json", "exec"),
    _r("POST", r"/networks/create", "network_create"),
    _r("POST", rf"/networks/(?P<id>{ID})/(?:connect|disconnect)", "network"),
    _r("DELETE", rf"/networks/(?P<id>{ID})", "network"),
    _r("GET", rf"/images/(?P<id>[A-Za-z0-9_./:@-]{{1,256}})/json"),
    _r("POST", r"/commit", "commit"),
)


def match(method: str, path: str) -> tuple[Route, dict] | None:
    for route in ROUTES:
        if route.method != method.upper():
            continue
        found = route.pattern.match(path)
        if found:
            return route, found.groupdict()
    return None
