"""Default-deny validation of ``POST /containers/create`` bodies.

Only the exact shape sandbox-manager generates is accepted. Anything unknown is
rejected, because a single missed field on a Docker socket equals host root.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

TOP_LEVEL = {"Image", "User", "Entrypoint", "Cmd", "WorkingDir", "Env", "HostConfig", "Labels"}
HOST_CONFIG = {"Runtime", "NetworkMode", "ReadonlyRootfs", "CapDrop", "CapAdd", "SecurityOpt",
               "PidsLimit", "Memory", "NanoCpus", "Tmpfs", "Mounts", "Privileged"}
MOUNT_KEYS = {"Type", "Source", "Target", "ReadOnly", "VolumeOptions"}
VOLUME_OPTION_KEYS = {"Subpath", "NoCopy"}
BUILD_ROLE = "skill-dependency-build"
BUILD_CAPS = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID"}
NETWORK_MODE = re.compile(r"^(none|[0-9a-f]{64}|movo-[A-Za-z0-9_.-]{1,120})$")
SUBPATH = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")
MAX_PIDS = 1024
MAX_MEMORY = 8 * 1024 ** 3
MAX_NANO_CPUS = 4_000_000_000


class Rejected(ValueError):
    pass


def _csv(value: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in value.split(",") if x.strip())


@dataclass(frozen=True)
class CreateRules:
    runtime: str = "kata"
    tenant_mode: str = "single"
    project: str = "movo"
    volumes: tuple[str, ...] = ("movo_sandbox-workspaces", "movo_dsh-runtime-data")
    image_prefixes: tuple[str, ...] = ("ghcr.io/himovo/movo-", "movo-skill-deps:")
    forbidden_volumes: tuple[str, ...] = field(default=("movo_deployment-secrets",))

    @classmethod
    def from_env(cls, env=None) -> "CreateRules":
        env = os.environ if env is None else env
        runtime = (env.get("MOVO_SANDBOX_RUNTIME") or "kata").strip().lower()
        mode = (env.get("MOVO_TENANT_MODE") or "single").strip().lower()
        if runtime not in {"kata", "runc"}:
            raise SystemExit(f"sock-proxy: unsupported MOVO_SANDBOX_RUNTIME={runtime!r}")
        if mode not in {"single", "multi"}:
            raise SystemExit(f"sock-proxy: unsupported MOVO_TENANT_MODE={mode!r}")
        if runtime == "runc" and mode == "multi":
            raise SystemExit("sock-proxy: runc is forbidden in multi-tenant mode (no override)")
        if runtime == "runc" and env.get("MOVO_SANDBOX_ALLOW_RUNC") != "i-understand":
            raise SystemExit("sock-proxy: runc requires MOVO_SANDBOX_ALLOW_RUNC=i-understand")
        prefix = env.get("MOVO_VOLUME_PREFIX", "movo")
        return cls(
            runtime=runtime, tenant_mode=mode,
            project=env.get("MOVO_SANDBOX_COMPOSE_PROJECT", "movo"),
            volumes=_csv(env.get("MOVO_SOCK_PROXY_VOLUMES", f"{prefix}_sandbox-workspaces,{prefix}_dsh-runtime-data")),
            image_prefixes=_csv(env.get("MOVO_SOCK_PROXY_IMAGE_PREFIXES",
                                        f"{env.get('MOVO_IMAGE_PREFIX', 'ghcr.io/himovo/movo')}-,movo-skill-deps:")),
            forbidden_volumes=(f"{prefix}_deployment-secrets",),
        )


def _require(cond: bool, reason: str) -> None:
    if not cond:
        raise Rejected(reason)


def validate_create(body: object, rules: CreateRules) -> None:
    _require(isinstance(body, dict), "body must be a JSON object")
    unknown = set(body) - TOP_LEVEL
    _require(not unknown, f"unknown top-level fields: {sorted(unknown)}")
    image = body.get("Image")
    _require(isinstance(image, str) and any(image.startswith(p) for p in rules.image_prefixes),
             "image is not in the allowed prefix list")
    labels = body.get("Labels") or {}
    _require(isinstance(labels, dict) and all(isinstance(v, str) for v in labels.values()), "Labels must be strings")
    _require(labels.get("movo.project") == rules.project, "missing movo.project label")
    role = labels.get("movo.role", "")
    _require(bool(role), "missing movo.role label")
    if rules.tenant_mode == "multi":
        _require(bool(labels.get("movo.tenant")), "movo.tenant label required in multi-tenant mode")
    user = str(body.get("User") or "")
    _require(bool(user), "User is required")
    if role != BUILD_ROLE:
        _require(user.split(":")[0] not in {"0", "root"}, "root User is not allowed")
    env = body.get("Env") or []
    _require(isinstance(env, list) and all(isinstance(e, str) for e in env), "Env must be a list of strings")
    for key in ("Entrypoint", "Cmd"):
        value = body.get(key)
        _require(value is None or (isinstance(value, list) and all(isinstance(v, str) for v in value)),
                 f"{key} must be a list of strings")

    host = body.get("HostConfig")
    _require(isinstance(host, dict), "HostConfig is required")
    unknown = set(host) - HOST_CONFIG
    _require(not unknown, f"unknown HostConfig fields: {sorted(unknown)}")
    _require(host.get("Runtime") == rules.runtime, f"Runtime must be {rules.runtime!r}")
    _require(host.get("Privileged") in (None, False), "Privileged is not allowed")
    _require(host.get("CapDrop") == ["ALL"], "CapDrop must be [ALL]")
    cap_add = host.get("CapAdd") or []
    if cap_add:
        _require(role == BUILD_ROLE and rules.runtime == "kata" and set(cap_add) <= BUILD_CAPS,
                 "CapAdd is only allowed for Kata skill builds with the fixed capability set")
    _require(host.get("SecurityOpt") == ["no-new-privileges:true"], "SecurityOpt must be exactly no-new-privileges")
    _require(isinstance(host.get("NetworkMode"), str) and NETWORK_MODE.fullmatch(host["NetworkMode"]) is not None,
             "NetworkMode must be a sandbox network")
    pids = host.get("PidsLimit")
    _require(isinstance(pids, int) and 0 < pids <= MAX_PIDS, "PidsLimit required and bounded")
    mem = host.get("Memory")
    _require(isinstance(mem, int) and 0 < mem <= MAX_MEMORY, "Memory required and bounded")
    cpus = host.get("NanoCpus")
    _require(isinstance(cpus, int) and 0 < cpus <= MAX_NANO_CPUS, "NanoCpus required and bounded")
    tmpfs = host.get("Tmpfs") or {}
    _require(isinstance(tmpfs, dict) and all(k == "/tmp" for k in tmpfs), "Tmpfs only allowed on /tmp")
    for opts in tmpfs.values():
        _require(isinstance(opts, str) and "nosuid" in opts.split(","), "Tmpfs must be nosuid")

    mounts = host.get("Mounts") or []
    _require(isinstance(mounts, list), "Mounts must be a list")
    for mount in mounts:
        _require(isinstance(mount, dict) and not set(mount) - MOUNT_KEYS, "unknown mount fields")
        _require(mount.get("Type") == "volume", "only volume mounts are allowed")
        source = mount.get("Source")
        _require(source not in rules.forbidden_volumes, "secrets volume must never be mounted into a sandbox")
        _require(source in rules.volumes, "volume is not a sandbox volume")
        target = mount.get("Target")
        _require(isinstance(target, str) and target.startswith("/") and ".." not in target, "invalid mount target")
        options = mount.get("VolumeOptions") or {}
        _require(isinstance(options, dict) and not set(options) - VOLUME_OPTION_KEYS, "unknown VolumeOptions")
        subpath = options.get("Subpath")
        _require(isinstance(subpath, str) and SUBPATH.fullmatch(subpath) is not None
                 and ".." not in subpath.split("/"), "mounts must use a safe Subpath")
