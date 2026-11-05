"""Central sandbox isolation policy (P0: Kata by default, fail-closed).

Every container the manager creates passes through ``SandboxPolicy.apply`` so
runtime selection and hardening cannot drift between jobs, task containers,
plugin runtimes and skill environment builds.

Rules (configuration contract):
- ``MOVO_SANDBOX_RUNTIME``: ``kata`` (default) or ``runc``; anything else fails.
- ``MOVO_TENANT_MODE``: ``single`` (default) or ``multi``.
- ``runc`` is only accepted in ``single`` mode *and* with
  ``MOVO_SANDBOX_ALLOW_RUNC=i-understand``. ``multi`` + ``runc`` is always
  rejected; there is no override switch.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Mapping

ALLOWED_RUNTIMES = ("kata", "runc")
TENANT_MODES = ("single", "multi")
RUNC_ACK = "i-understand"
DEFAULT_PIDS_LIMIT = 256
DEFAULT_MEMORY = 1024 * 1024 * 1024
DEFAULT_NANO_CPUS = 1_000_000_000
NO_NEW_PRIVS = "no-new-privileges:true"
# Skill dependency builds may install apt packages declared by the skill. They
# are allowed a fixed, minimal capability set *only* inside a Kata VM, where
# guest root is not host root. Under the runc fallback they are refused.
BUILD_ROLE = "skill-dependency-build"
BUILD_CAPS = frozenset({"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID"})


class PolicyViolation(RuntimeError):
    """Raised when configuration or a payload violates the sandbox policy."""


@dataclass(frozen=True)
class SandboxPolicy:
    runtime: str = "kata"
    tenant_mode: str = "single"
    allow_runc_fallback: bool = False
    project: str = "movo"

    @property
    def fallback(self) -> bool:
        return self.runtime == "runc"

    def apply(self, payload: dict, tenant_id: str | None = None, role: str | None = None) -> dict:
        """Return a copy of ``payload`` with runtime + hardening enforced."""
        if self.tenant_mode == "multi" and not tenant_id:
            raise PolicyViolation("tenant_id is required in multi-tenant mode")
        result = copy.deepcopy(payload)
        host = result.setdefault("HostConfig", {})
        cap_add = host.get("CapAdd") or []
        if cap_add:
            if role != BUILD_ROLE or not set(cap_add) <= BUILD_CAPS:
                raise PolicyViolation("HostConfig.CapAdd is not allowed")
            if self.runtime != "kata":
                raise PolicyViolation("Skill dependency builds with elevated capabilities require the Kata runtime")
        for forbidden in ("Privileged", "Devices", "Binds"):
            if host.get(forbidden):
                raise PolicyViolation(f"HostConfig.{forbidden} is not allowed")
        for ns in ("PidMode", "IpcMode", "UTSMode", "NetworkMode", "UsernsMode", "CgroupnsMode"):
            if str(host.get(ns) or "").lower() == "host":
                raise PolicyViolation(f"HostConfig.{ns}=host is not allowed")
        user = str(result.get("User") or "").strip()
        if role != BUILD_ROLE and (not user or user.split(":")[0] in {"0", "root"}):
            raise PolicyViolation("Sandbox containers must run as a non-root User")
        host["Runtime"] = self.runtime
        host["Privileged"] = False
        host["CapDrop"] = ["ALL"]
        opts = [o for o in host.get("SecurityOpt") or [] if o != NO_NEW_PRIVS]
        if opts:
            raise PolicyViolation("Only no-new-privileges is allowed in SecurityOpt")
        host["SecurityOpt"] = [NO_NEW_PRIVS]
        host.setdefault("ReadonlyRootfs", True)
        host["PidsLimit"] = min(int(host.get("PidsLimit") or DEFAULT_PIDS_LIMIT), 1024)
        host.setdefault("Memory", DEFAULT_MEMORY)
        host.setdefault("NanoCpus", DEFAULT_NANO_CPUS)
        labels = result.setdefault("Labels", {})
        labels["movo.project"] = self.project
        labels["movo.runtime"] = self.runtime
        if tenant_id:
            labels["movo.tenant"] = tenant_id
        if role:
            labels["movo.role"] = role
        labels.setdefault("movo.role", "sandbox")
        return result


def load_policy(env: Mapping[str, str] | None = None) -> SandboxPolicy:
    env = os.environ if env is None else env
    runtime = (env.get("MOVO_SANDBOX_RUNTIME") or "kata").strip().lower()
    tenant_mode = (env.get("MOVO_TENANT_MODE") or "single").strip().lower()
    ack = (env.get("MOVO_SANDBOX_ALLOW_RUNC") or "").strip()
    if runtime not in ALLOWED_RUNTIMES:
        raise PolicyViolation(f"MOVO_SANDBOX_RUNTIME must be one of {ALLOWED_RUNTIMES}, got {runtime!r}")
    if tenant_mode not in TENANT_MODES:
        raise PolicyViolation(f"MOVO_TENANT_MODE must be one of {TENANT_MODES}, got {tenant_mode!r}")
    if runtime == "runc":
        if tenant_mode == "multi":
            raise PolicyViolation("runc fallback is forbidden in multi-tenant mode (no override)")
        if ack != RUNC_ACK:
            raise PolicyViolation(
                f"runc fallback requires MOVO_SANDBOX_ALLOW_RUNC={RUNC_ACK} in single-tenant mode")
    return SandboxPolicy(
        runtime=runtime,
        tenant_mode=tenant_mode,
        allow_runc_fallback=runtime == "runc",
        project=env.get("MOVO_SANDBOX_COMPOSE_PROJECT", "movo"),
    )


_cached: SandboxPolicy | None = None


def current_policy() -> SandboxPolicy:
    global _cached
    if _cached is None:
        _cached = load_policy()
    return _cached


def reset_policy_cache() -> None:
    global _cached
    _cached = None


TENANT_ID = __import__("re").compile(r"^[A-Za-z0-9_.-]{1,64}$")


def read_tenant_marker(directory) -> str | None:
    """Read the optional ``tenant-id`` marker chat-api writes into a job/task dir."""
    from pathlib import Path
    marker = Path(directory) / "tenant-id"
    if not marker.is_file():
        return None
    value = marker.read_text(encoding="ascii").strip()
    if not TENANT_ID.fullmatch(value):
        raise ValueError("Invalid tenant identifier")
    return value
