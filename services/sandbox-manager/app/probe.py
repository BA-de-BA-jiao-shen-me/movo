"""Kata runtime probe: VM kernel + network reachability of sandbox-egress.

Requires a KVM host; on hosts without Kata the probe fails and the manager
stops accepting work (fail-closed) unless an allowed runc fallback is active.
"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass

from .docker_engine import split_docker_logs
from .sandbox_policy import SandboxPolicy

PROBE_SCRIPT = (
    "import os,socket,platform;"
    "print('kernel='+platform.release());"
    "print('eth0='+str(os.path.exists('/sys/class/net/eth0')));"
    "s=socket.create_connection(('sandbox-egress',3128),5);s.close();print('egress=ok')"
)


@dataclass
class ProbeResult:
    ok: bool
    kernel: str = ""
    egress_ok: bool = False
    error: str = ""
    checked_at: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def parse_probe_output(stdout: str, host_kernel: str, runtime: str) -> ProbeResult:
    kernel = (re.search(r"^kernel=(.+)$", stdout, re.M) or [None, ""])[1].strip()
    eth0 = "eth0=True" in stdout
    egress = "egress=ok" in stdout
    error = ""
    if not eth0:
        error = "sandbox has no eth0 (Docker 28+ Kata networking issue?)"
    elif not egress:
        error = "sandbox cannot reach sandbox-egress:3128"
    elif runtime == "kata" and kernel == host_kernel:
        error = "sandbox kernel equals host kernel; not running inside a Kata VM"
    return ProbeResult(ok=not error, kernel=kernel, egress_ok=egress, error=error, checked_at=time.time())


def run_probe(engine, policy: SandboxPolicy, image: str | None = None) -> ProbeResult:
    image = image or os.environ.get("MOVO_SANDBOX_SCRIPT_IMAGE", "")
    project = os.environ.get("MOVO_SANDBOX_COMPOSE_PROJECT", "movo")
    probe_id = uuid.uuid4().hex
    container_id = network_id = proxy_id = None
    try:
        host_kernel = str(engine.request("GET", "/info").json().get("KernelVersion", ""))
        proxy_id = engine.find_compose_service(project, "sandbox-egress")
        network_id = engine.create_internal_network(f"movo-probe-{probe_id}")
        engine.connect_network(network_id, proxy_id, ["sandbox-egress"])
        payload = policy.apply({
            "Image": image, "User": "65534:65534",
            "Entrypoint": ["python"], "Cmd": ["-I", "-c", PROBE_SCRIPT],
            "HostConfig": {"NetworkMode": network_id, "Tmpfs": {"/tmp": "rw,nosuid,noexec,size=8m"}},
        }, role="probe")
        container_id = engine.create(f"movo-probe-{probe_id}", payload)
        engine.start(container_id)
        engine.wait(container_id, 60)
        stdout, _ = split_docker_logs(engine.logs(container_id))
        return parse_probe_output(stdout.decode("utf-8", "replace"), host_kernel, policy.runtime)
    except Exception as exc:  # noqa: BLE001 - surfaced in /health
        return ProbeResult(ok=False, error=f"probe failed: {exc}", checked_at=time.time())
    finally:
        for action in (
            lambda: container_id and engine.remove(container_id),
            lambda: network_id and proxy_id and engine.disconnect_network(network_id, proxy_id),
            lambda: network_id and engine.remove_network(network_id),
        ):
            try:
                action()
            except Exception:  # noqa: BLE001
                pass


class ProbeMonitor:
    """Caches the latest probe result and refreshes it periodically."""

    def __init__(self, runner, interval: float | None = None) -> None:
        self._runner = runner
        self.interval = float(interval if interval is not None else os.environ.get("MOVO_SANDBOX_PROBE_INTERVAL", "600"))
        self._lock = threading.Lock()
        self._last = ProbeResult(ok=False, error="probe not run yet")

    def refresh(self) -> ProbeResult:
        result = self._runner()
        with self._lock:
            self._last = result
        return result

    def latest(self) -> ProbeResult:
        with self._lock:
            last = self._last
        if not last.checked_at or time.time() - last.checked_at > self.interval:
            return self.refresh()
        return last
