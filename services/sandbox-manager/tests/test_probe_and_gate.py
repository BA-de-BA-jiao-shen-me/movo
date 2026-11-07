from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app import main, sandbox_policy
from app.probe import ProbeMonitor, ProbeResult, parse_probe_output

TOKEN = "a" * 64
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def test_parse_probe_output_variants() -> None:
    good = "kernel=6.1.62-kata\neth0=True\negress=ok\n"
    assert parse_probe_output(good, "6.8.0-host", "kata").ok
    assert "eth0" in parse_probe_output("kernel=6.1\neth0=False\n", "6.8", "kata").error
    assert "egress" in parse_probe_output("kernel=6.1\neth0=True\n", "6.8", "kata").error
    same = parse_probe_output("kernel=6.8\neth0=True\negress=ok\n", "6.8", "kata")
    assert not same.ok and "Kata VM" in same.error
    assert parse_probe_output("kernel=6.8\neth0=True\negress=ok\n", "6.8", "runc").ok


def test_probe_monitor_caches_and_refreshes() -> None:
    calls = []

    def runner():
        calls.append(1)
        return ProbeResult(ok=True, checked_at=time.time())

    monitor = ProbeMonitor(runner, interval=600)
    assert monitor.latest().ok and monitor.latest().ok
    assert len(calls) == 1


def _setup(monkeypatch, tmp_path, probe_ok: bool):
    monkeypatch.setenv("MOVO_SANDBOX_MANAGER_TOKEN", TOKEN)
    monkeypatch.setenv("MOVO_SANDBOX_AUDIT_LOG", str(tmp_path / "audit.log"))
    monkeypatch.delenv("MOVO_SANDBOX_PROBE_REQUIRED", raising=False)
    sandbox_policy.reset_policy_cache()
    monitor = ProbeMonitor(lambda: ProbeResult(ok=probe_ok, error="" if probe_ok else "no kvm",
                                               checked_at=time.time()))
    monkeypatch.setattr(main, "probe", lambda: monitor)


def test_failed_probe_refuses_all_execution(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path, probe_ok=False)

    class Boom:
        def __getattr__(self, name):
            raise AssertionError("must not execute when sandbox is unavailable")

    monkeypatch.setattr(main, "jobs", lambda: Boom())
    monkeypatch.setattr(main, "tasks", lambda: Boom())
    monkeypatch.setattr(main, "plugins", lambda: Boom())
    client = TestClient(main.app)
    for path, body in [
        ("/v1/script-jobs", {"job_id": "a" * 32}),
        ("/v1/script-task-calls", {"task_id": "a" * 32, "call_id": "b" * 32}),
        ("/v1/plugin-runtimes", {"isolation_key": "k"}),
        ("/v1/skill-environments", {"digest": "c" * 64}),
    ]:
        response = client.post(path, json=body, headers=HEADERS)
        assert response.status_code == 503, path
        assert response.json()["detail"]["code"] == "sandbox_unavailable"
    assert "sandbox.denied" in (tmp_path / "audit.log").read_text()


def test_passing_probe_executes_and_audits(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path, probe_ok=True)

    class JobsFake:
        def execute(self, job_id):
            return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(main, "jobs", lambda: JobsFake())
    response = TestClient(main.app).post("/v1/script-jobs", json={"job_id": "a" * 32}, headers=HEADERS)
    assert response.status_code == 200
    assert "sandbox.exit" in (tmp_path / "audit.log").read_text()


def test_multi_tenant_runc_refuses_startup(monkeypatch) -> None:
    monkeypatch.setenv("MOVO_SANDBOX_RUNTIME", "runc")
    monkeypatch.setenv("MOVO_TENANT_MODE", "multi")
    monkeypatch.setenv("MOVO_SANDBOX_ALLOW_RUNC", "i-understand")
    sandbox_policy.reset_policy_cache()
    try:
        try:
            with TestClient(main.app):
                pass
        except sandbox_policy.PolicyViolation:
            return
        raise AssertionError("startup must fail")
    finally:
        sandbox_policy.reset_policy_cache()


def test_health_reports_runtime_and_probe(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path, probe_ok=False)

    class S:
        def status(self):
            return {}

    monkeypatch.setattr(main, "jobs", lambda: S())
    monkeypatch.setattr(main, "plugins", lambda: S())
    body = TestClient(main.app).get("/health", headers=HEADERS).json()
    assert body["runtime"] == "kata" and body["fallback"] is False and body["probe_ok"] is False
