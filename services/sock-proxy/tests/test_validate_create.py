from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings, strategies as st

from app.validate_create import CreateRules, Rejected, validate_create

RULES = CreateRules()
NET = "a" * 64


def good() -> dict:
    return {
        "Image": "ghcr.io/himovo/movo-sandbox-script:latest",
        "User": "65534:65534",
        "Entrypoint": ["python"], "Cmd": ["-I", "/job/runner.py"], "WorkingDir": "/job",
        "Env": ["HOME=/tmp"],
        "HostConfig": {
            "Runtime": "kata", "NetworkMode": NET, "ReadonlyRootfs": True, "Privileged": False,
            "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": 64, "Memory": 1 << 30, "NanoCpus": 1_000_000_000,
            "Tmpfs": {"/tmp": "rw,nosuid,noexec,size=64m"},
            "Mounts": [{"Type": "volume", "Source": "movo_sandbox-workspaces", "Target": "/job",
                        "VolumeOptions": {"Subpath": "a" * 32, "NoCopy": True}}],
        },
        "Labels": {"movo.project": "movo", "movo.role": "script-job", "movo.runtime": "kata"},
    }


def test_good_payload_passes() -> None:
    validate_create(good(), RULES)


def _mut(path, value):
    body = good()
    target = body
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        target.pop(path[-1], None)
    else:
        target[path[-1]] = value
    return body


BAD = [
    ("not-object", []),
    ("unknown top", _mut(["Volumes"], {"/x": {}})),
    ("unknown top2", _mut(["HostConfig2"], {})),
    ("privileged", _mut(["HostConfig", "Privileged"], True)),
    ("capadd", _mut(["HostConfig", "CapAdd"], ["SYS_ADMIN"])),
    ("capadd build caps not build", _mut(["HostConfig", "CapAdd"], ["CHOWN"])),
    ("devices", _mut(["HostConfig", "Devices"], [{"PathOnHost": "/dev/kvm"}])),
    ("binds", _mut(["HostConfig", "Binds"], ["/:/host"])),
    ("pid host", _mut(["HostConfig", "PidMode"], "host")),
    ("ipc host", _mut(["HostConfig", "IpcMode"], "host")),
    ("uts host", _mut(["HostConfig", "UTSMode"], "host")),
    ("userns host", _mut(["HostConfig", "UsernsMode"], "host")),
    ("net host", _mut(["HostConfig", "NetworkMode"], "host")),
    ("net bridge", _mut(["HostConfig", "NetworkMode"], "bridge")),
    ("net container", _mut(["HostConfig", "NetworkMode"], "container:abc")),
    ("runtime runc", _mut(["HostConfig", "Runtime"], "runc")),
    ("runtime missing", _mut(["HostConfig", "Runtime"], KeyError)),
    ("seccomp", _mut(["HostConfig", "SecurityOpt"], ["seccomp=unconfined"])),
    ("apparmor", _mut(["HostConfig", "SecurityOpt"], ["no-new-privileges:true", "apparmor=unconfined"])),
    ("no secopt", _mut(["HostConfig", "SecurityOpt"], KeyError)),
    ("capdrop partial", _mut(["HostConfig", "CapDrop"], ["NET_RAW"])),
    ("no pids", _mut(["HostConfig", "PidsLimit"], KeyError)),
    ("pids huge", _mut(["HostConfig", "PidsLimit"], 10 ** 6)),
    ("pids -1", _mut(["HostConfig", "PidsLimit"], -1)),
    ("mem huge", _mut(["HostConfig", "Memory"], 1 << 40)),
    ("no cpu", _mut(["HostConfig", "NanoCpus"], KeyError)),
    ("cgroup parent", _mut(["HostConfig", "CgroupParent"], "/")),
    ("sysctls", _mut(["HostConfig", "Sysctls"], {"kernel.x": "1"})),
    ("tmpfs elsewhere", _mut(["HostConfig", "Tmpfs"], {"/etc": "rw"})),
    ("tmpfs suid", _mut(["HostConfig", "Tmpfs"], {"/tmp": "rw,exec"})),
    ("bind mount", _mut(["HostConfig", "Mounts"], [{"Type": "bind", "Source": "/", "Target": "/h"}])),
    ("secrets vol", _mut(["HostConfig", "Mounts"], [{"Type": "volume", "Source": "movo_deployment-secrets",
                                                     "Target": "/s", "VolumeOptions": {"Subpath": "x"}}])),
    ("other vol", _mut(["HostConfig", "Mounts"], [{"Type": "volume", "Source": "movo_mongo-data",
                                                   "Target": "/d", "VolumeOptions": {"Subpath": "x"}}])),
    ("no subpath", _mut(["HostConfig", "Mounts"], [{"Type": "volume", "Source": "movo_sandbox-workspaces",
                                                    "Target": "/job"}])),
    ("dotdot subpath", _mut(["HostConfig", "Mounts"], [{"Type": "volume", "Source": "movo_sandbox-workspaces",
                                                        "Target": "/job", "VolumeOptions": {"Subpath": "a/../.."}}])),
    ("abs subpath", _mut(["HostConfig", "Mounts"], [{"Type": "volume", "Source": "movo_sandbox-workspaces",
                                                     "Target": "/job", "VolumeOptions": {"Subpath": "/etc"}}])),
    ("driver opts", _mut(["HostConfig", "Mounts"], [{"Type": "volume", "Source": "movo_sandbox-workspaces",
                                                     "Target": "/job", "VolumeOptions": {
                                                         "Subpath": "x", "DriverConfig": {"Name": "local"}}}])),
    ("propagation", _mut(["HostConfig", "Mounts"], [{"Type": "volume", "Source": "movo_sandbox-workspaces",
                                                     "Target": "/job", "BindOptions": {"Propagation": "rshared"},
                                                     "VolumeOptions": {"Subpath": "x"}}])),
    ("image", _mut(["Image"], "docker.io/library/alpine")),
    ("image prefix trick", _mut(["Image"], "evil.io/ghcr.io/himovo/movo-x")),
    ("no project", _mut(["Labels", "movo.project"], KeyError)),
    ("wrong project", _mut(["Labels", "movo.project"], "other")),
    ("no role", _mut(["Labels", "movo.role"], KeyError)),
    ("root user", _mut(["User"], "0:0")),
    ("root name", _mut(["User"], "root")),
    ("no user", _mut(["User"], KeyError)),
    ("env non-string", _mut(["Env"], [1])),
    ("cmd string", _mut(["Cmd"], "sh -c id")),
]


@pytest.mark.parametrize("name,body", BAD, ids=[b[0] for b in BAD])
def test_bad_payloads_rejected(name, body) -> None:
    with pytest.raises(Rejected):
        validate_create(body, RULES)


def test_build_role_caps_only_under_kata() -> None:
    body = good()
    body["User"] = "0:0"
    body["Labels"]["movo.role"] = "skill-dependency-build"
    body["HostConfig"]["CapAdd"] = ["CHOWN", "SETUID"]
    validate_create(body, RULES)
    with pytest.raises(Rejected):
        validate_create(body, CreateRules(runtime="runc"))
    body["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]
    with pytest.raises(Rejected):
        validate_create(body, RULES)


def test_multi_tenant_requires_tenant_label() -> None:
    rules = CreateRules(tenant_mode="multi")
    with pytest.raises(Rejected):
        validate_create(good(), rules)
    body = good()
    body["Labels"]["movo.tenant"] = "org1"
    validate_create(body, rules)


@pytest.mark.parametrize("env", [
    {"MOVO_SANDBOX_RUNTIME": "runc", "MOVO_TENANT_MODE": "multi", "MOVO_SANDBOX_ALLOW_RUNC": "i-understand"},
    {"MOVO_SANDBOX_RUNTIME": "runc"},
    {"MOVO_SANDBOX_RUNTIME": "runsc"},
    {"MOVO_TENANT_MODE": "x"},
])
def test_rules_refuse_unsafe_startup(env) -> None:
    with pytest.raises(SystemExit):
        CreateRules.from_env(env)


def test_rules_runc_single_with_ack() -> None:
    rules = CreateRules.from_env({"MOVO_SANDBOX_RUNTIME": "runc", "MOVO_SANDBOX_ALLOW_RUNC": "i-understand",
                                  "MOVO_VOLUME_PREFIX": "p"})
    assert rules.runtime == "runc" and rules.forbidden_volumes == ("p_deployment-secrets",)


DANGEROUS_HOST = st.sampled_from([
    ("Privileged", True), ("CapAdd", ["SYS_ADMIN"]), ("Devices", [{"PathOnHost": "/dev/sda"}]),
    ("Binds", ["/var/run/docker.sock:/s"]), ("PidMode", "host"), ("NetworkMode", "host"),
    ("SecurityOpt", ["seccomp=unconfined"]), ("Runtime", "runc"), ("CapDrop", []),
    ("CgroupParent", "/"), ("OomKillDisable", True), ("DeviceCgroupRules", ["a *:* rwm"]),
])
JUNK = st.recursive(st.none() | st.booleans() | st.integers() | st.text(max_size=8),
                    lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=8), c, max_size=3),
                    max_leaves=10)


@settings(max_examples=300, deadline=None)
@given(DANGEROUS_HOST)
def test_fuzz_any_dangerous_host_field_rejected(item) -> None:
    key, value = item
    body = good()
    body["HostConfig"][key] = value
    with pytest.raises(Rejected):
        validate_create(body, RULES)


@settings(max_examples=500, deadline=None)
@given(st.text(min_size=1, max_size=20).filter(lambda k: k not in {
    "Image", "User", "Entrypoint", "Cmd", "WorkingDir", "Env", "HostConfig", "Labels"}), JUNK)
def test_fuzz_unknown_top_level_rejected(key, value) -> None:
    body = good()
    body[key] = value
    with pytest.raises(Rejected):
        validate_create(body, RULES)


@settings(max_examples=500, deadline=None)
@given(JUNK)
def test_fuzz_random_bodies_never_crash(value) -> None:
    try:
        validate_create(value, RULES)
    except Rejected:
        pass
    else:  # pragma: no cover - random junk must never be accepted
        raise AssertionError(f"accepted {value!r}")


@settings(max_examples=300, deadline=None)
@given(st.sampled_from(["HostConfig", "Labels", "Env", "User", "Image"]), JUNK)
def test_fuzz_replacing_fields_with_junk_never_crashes(key, value) -> None:
    body = copy.deepcopy(good())
    body[key] = value
    try:
        validate_create(body, RULES)
    except Rejected:
        pass
