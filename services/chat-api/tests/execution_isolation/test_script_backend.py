from __future__ import annotations

from pathlib import Path

import pytest

from app.execution_isolation import script_backend


def test_container_workspace_paths_are_job_scoped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOVO_SANDBOX_MANAGER_URL", "http://sandbox-manager:8300")
    monkeypatch.setenv("MOVO_SANDBOX_WORKSPACE_ROOT", str(tmp_path))
    with script_backend.script_workspace() as work_dir:
        assert work_dir.parent == tmp_path
        assert len(work_dir.name) == 32
        assert script_backend.child_path(work_dir / "inputs" / "file.docx", work_dir) == "/job/inputs/file.docx"
        assert work_dir.exists()
    assert not work_dir.exists()


def test_configured_backend_fails_closed_without_credential(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOVO_SANDBOX_MANAGER_URL", "http://sandbox-manager:8300")
    monkeypatch.delenv("MOVO_SANDBOX_MANAGER_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="token is missing"):
        script_backend.run_script_job(
            work_dir=tmp_path, runner_path=tmp_path / "runner.py", plugin_path=tmp_path / "plugin.py",
            stdin_path=tmp_path / "stdin.json", env={}, timeout_seconds=1,
            limit_child_process=lambda: None,
        )


def test_task_workspace_persists_between_calls(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOVO_SANDBOX_MANAGER_URL", "http://sandbox-manager:8300")
    monkeypatch.setenv("MOVO_SANDBOX_WORKSPACE_ROOT", str(tmp_path))
    with script_backend.script_workspace("message-1") as work:
        (work / "intermediate.txt").write_text("kept")
        assert script_backend.child_path(work / "intermediate.txt", work) == "/job/work/intermediate.txt"
    with script_backend.script_workspace("message-1") as work:
        assert (work / "intermediate.txt").read_text() == "kept"
    with script_backend.script_workspace("message-2") as other:
        assert not (other / "intermediate.txt").exists()


def test_no_manager_url_fails_closed_instead_of_running_in_process(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MOVO_SANDBOX_MANAGER_URL", raising=False)
    monkeypatch.delenv("MOVO_DEV_INPROCESS_SCRIPTS", raising=False)
    with pytest.raises(script_backend.SandboxUnavailable):
        with script_backend.script_workspace():
            pass
    with pytest.raises(script_backend.SandboxUnavailable):
        script_backend.run_script_job(
            work_dir=tmp_path, runner_path=tmp_path / "runner.py", plugin_path=tmp_path / "plugin.py",
            stdin_path=tmp_path / "stdin.json", env={}, timeout_seconds=1,
            limit_child_process=lambda: None,
        )


def test_dev_inprocess_mode_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv("MOVO_SANDBOX_MANAGER_URL", raising=False)
    monkeypatch.setenv("MOVO_DEV_INPROCESS_SCRIPTS", "1")
    with script_backend.script_workspace() as work:
        assert work.exists()


def test_manager_503_maps_to_sandbox_unavailable(tmp_path: Path, monkeypatch) -> None:
    import httpx

    monkeypatch.setenv("MOVO_SANDBOX_MANAGER_URL", "http://sandbox-manager:8300")
    monkeypatch.setenv("MOVO_SANDBOX_MANAGER_TOKEN", "t" * 64)

    def handler(request):
        return httpx.Response(503, json={"detail": {"code": "sandbox_unavailable", "reason": "probe failed"}})

    real_client = httpx.Client
    monkeypatch.setattr(script_backend.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(script_backend.SandboxUnavailable, match="probe failed"):
        script_backend.run_script_job(
            work_dir=tmp_path, runner_path=tmp_path / "r", plugin_path=tmp_path / "p",
            stdin_path=tmp_path / "s", env={}, timeout_seconds=1, limit_child_process=lambda: None,
        )


def test_manager_unreachable_maps_to_sandbox_unavailable(tmp_path: Path, monkeypatch) -> None:
    import httpx

    monkeypatch.setenv("MOVO_SANDBOX_MANAGER_URL", "http://sandbox-manager:8300")
    monkeypatch.setenv("MOVO_SANDBOX_MANAGER_TOKEN", "t" * 64)

    def handler(request):
        raise httpx.ConnectError("refused")

    real_client = httpx.Client
    monkeypatch.setattr(script_backend.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(script_backend.SandboxUnavailable, match="unreachable"):
        script_backend.run_script_job(
            work_dir=tmp_path, runner_path=tmp_path / "r", plugin_path=tmp_path / "p",
            stdin_path=tmp_path / "s", env={}, timeout_seconds=1, limit_child_process=lambda: None,
        )


def test_tenant_marker_written_from_request_context(tmp_path: Path, monkeypatch) -> None:
    import httpx

    from app.infrastructure.request_context import reset_request_context, set_request_context

    monkeypatch.setenv("MOVO_SANDBOX_MANAGER_URL", "http://sandbox-manager:8300")
    monkeypatch.setenv("MOVO_SANDBOX_MANAGER_TOKEN", "t" * 64)
    real_client = httpx.Client
    monkeypatch.setattr(script_backend.httpx, "Client", lambda **kw: real_client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"exit_code": 0})), **kw))
    previous = set_request_context({"main_id": "org_7"})
    try:
        script_backend.run_script_job(
            work_dir=tmp_path, runner_path=tmp_path / "r", plugin_path=tmp_path / "p",
            stdin_path=tmp_path / "s", env={}, timeout_seconds=1, limit_child_process=lambda: None,
        )
    finally:
        reset_request_context(previous)
    assert (tmp_path / "tenant-id").read_text() == "org_7"
