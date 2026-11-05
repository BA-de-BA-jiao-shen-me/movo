from __future__ import annotations

import itertools

import httpx
import pytest

from app import audit
from app.docker_engine import DockerEngine
from app.sandbox_policy import (BUILD_ROLE, PolicyViolation, SandboxPolicy, load_policy,
                                read_tenant_marker)

ACK = {"MOVO_SANDBOX_ALLOW_RUNC": "i-understand"}


@pytest.mark.parametrize("runtime,mode,allow", list(itertools.product(
    ["kata", "runc", "runsc", ""], ["single", "multi", "bogus"], [None, "i-understand", "yes"])))
def test_policy_matrix(runtime, mode, allow) -> None:
    env = {"MOVO_SANDBOX_RUNTIME": runtime, "MOVO_TENANT_MODE": mode}
    if allow:
        env["MOVO_SANDBOX_ALLOW_RUNC"] = allow
    effective = runtime or "kata"
    ok = (mode in {"single", "multi"} and (
        effective == "kata" or (effective == "runc" and mode == "single" and allow == "i-understand")))
    if ok:
        policy = load_policy(env)
        assert policy.runtime == effective
        assert policy.fallback == (effective == "runc")
    else:
        with pytest.raises(PolicyViolation):
            load_policy(env)


def test_multi_tenant_runc_has_no_override() -> None:
    with pytest.raises(PolicyViolation, match="multi-tenant"):
        load_policy({"MOVO_SANDBOX_RUNTIME": "runc", "MOVO_TENANT_MODE": "multi", **ACK})


def test_defaults_are_kata_single() -> None:
    policy = load_policy({})
    assert (policy.runtime, policy.tenant_mode, policy.fallback) == ("kata", "single", False)


def _payload(**host):
    return {"Image": "x", "User": "65534:65534", "HostConfig": dict(host), "Labels": {}}


def test_apply_enforces_runtime_and_hardening() -> None:
    out = SandboxPolicy().apply(_payload(ReadonlyRootfs=True, PidsLimit=64), tenant_id="t1", role="script-job")
    host = out["HostConfig"]
    assert host["Runtime"] == "kata"
    assert host["CapDrop"] == ["ALL"]
    assert host["SecurityOpt"] == ["no-new-privileges:true"]
    assert host["PidsLimit"] == 64 and host["Memory"] and host["NanoCpus"]
    assert out["Labels"] == {"movo.project": "movo", "movo.runtime": "kata", "movo.tenant": "t1",
                             "movo.role": "script-job"}


def test_apply_does_not_mutate_input() -> None:
    payload = _payload()
    SandboxPolicy().apply(payload)
    assert payload["HostConfig"] == {}


@pytest.mark.parametrize("host", [
    {"Privileged": True}, {"CapAdd": ["SYS_ADMIN"]}, {"Devices": [{"PathOnHost": "/dev/kvm"}]},
    {"Binds": ["/:/host"]}, {"PidMode": "host"}, {"IpcMode": "host"}, {"UTSMode": "host"},
    {"NetworkMode": "host"}, {"UsernsMode": "host"}, {"SecurityOpt": ["seccomp=unconfined"]},
    {"SecurityOpt": ["apparmor=unconfined", "no-new-privileges:true"]},
])
def test_apply_rejects_dangerous_host_config(host) -> None:
    with pytest.raises(PolicyViolation):
        SandboxPolicy().apply(_payload(**host))


@pytest.mark.parametrize("user", ["", "0", "0:0", "root", "root:root"])
def test_root_user_rejected_outside_builds(user) -> None:
    payload = _payload()
    payload["User"] = user
    with pytest.raises(PolicyViolation):
        SandboxPolicy().apply(payload, role="script-job")


def test_build_caps_only_under_kata() -> None:
    payload = _payload(CapAdd=["CHOWN", "SETUID"])
    payload["User"] = "0:0"
    assert SandboxPolicy().apply(payload, role=BUILD_ROLE)["HostConfig"]["Runtime"] == "kata"
    with pytest.raises(PolicyViolation, match="Kata"):
        SandboxPolicy(runtime="runc", allow_runc_fallback=True).apply(payload, role=BUILD_ROLE)
    with pytest.raises(PolicyViolation):
        SandboxPolicy().apply(_payload(CapAdd=["SYS_ADMIN"]), role=BUILD_ROLE)


def test_multi_tenant_requires_tenant() -> None:
    with pytest.raises(PolicyViolation):
        SandboxPolicy(tenant_mode="multi").apply(_payload())


def test_tenant_marker(tmp_path) -> None:
    assert read_tenant_marker(tmp_path) is None
    (tmp_path / "tenant-id").write_text("org_42\n")
    assert read_tenant_marker(tmp_path) == "org_42"
    (tmp_path / "tenant-id").write_text("../evil")
    with pytest.raises(ValueError):
        read_tenant_marker(tmp_path)


def test_engine_create_always_applies_policy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOVO_SANDBOX_AUDIT_LOG", str(tmp_path / "audit.log"))
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(201, json={"Id": "abc123def456789"})

    engine = DockerEngine("http://sock-proxy:2375", policy=SandboxPolicy())
    engine.client = httpx.Client(base_url="http://sock-proxy:2375/v1.45", transport=httpx.MockTransport(handler))
    engine.create("n", _payload())
    assert seen["url"].startswith("http://sock-proxy:2375/v1.45/containers/create")
    assert b'"Runtime":"kata"' in seen["body"].replace(b" ", b"")
    assert '"sandbox.create"' in (tmp_path / "audit.log").read_text()


def test_engine_defaults_to_sock_proxy(monkeypatch) -> None:
    monkeypatch.delenv("MOVO_DOCKER_HOST", raising=False)
    assert str(DockerEngine().client.base_url) == "http://sock-proxy:2375/v1.45/"


def test_audit_rejects_unknown_event(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOVO_SANDBOX_AUDIT_LOG", str(tmp_path / "a.log"))
    with pytest.raises(ValueError):
        audit.emit("whatever")
    record = audit.emit("sandbox.fallback_runc", tenant=None, runtime="runc")
    assert "tenant" not in record
