"""Narrow control-plane API for per-job containers."""

from __future__ import annotations

import hmac
import os
import threading
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import audit
from .docker_engine import DockerEngine
from .probe import ProbeMonitor, run_probe
from .sandbox_policy import TENANT_ID, PolicyViolation, current_policy
from .jobs import ScriptJobs
from .plugin_runtimes import PluginRuntimes
from .skill_environments import DependencyPreparationError
from .task_containers import TaskContainers


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_probe: ProbeMonitor | None = None


@app.on_event("startup")
def _load_policy_or_exit() -> None:
    """Fail-closed: an invalid policy (e.g. multi-tenant + runc) aborts startup."""
    policy = current_policy()  # raises PolicyViolation -> uvicorn exits
    if policy.fallback:
        audit.emit("sandbox.fallback_runc", runtime=policy.runtime, tenant_mode=policy.tenant_mode,
                   reason="administrator enabled runc fallback", phase="startup")


def probe() -> ProbeMonitor:
    global _probe
    with _jobs_lock:
        if _probe is None:
            _probe = ProbeMonitor(lambda: run_probe(DockerEngine(), current_policy()))
    return _probe


def _require_sandbox(kind: str, tenant_id: str | None = None) -> None:
    """Refuse work unless the policy is valid and the runtime probe passes."""
    try:
        policy = current_policy()
    except PolicyViolation as exc:
        raise HTTPException(status_code=503, detail={"code": "sandbox_unavailable", "reason": str(exc)}) from exc
    if os.environ.get("MOVO_SANDBOX_PROBE_REQUIRED", "1") != "0":
        result = probe().latest()
        if not result.ok:
            audit.emit("sandbox.denied", kind=kind, tenant=tenant_id, runtime=policy.runtime, reason=result.error)
            raise HTTPException(status_code=503, detail={"code": "sandbox_unavailable", "reason": result.error})
    if policy.tenant_mode == "multi" and kind == "plugin-runtime" and not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required in multi-tenant mode")
    if policy.fallback:
        audit.emit("sandbox.fallback_runc", kind=kind, tenant=tenant_id, runtime=policy.runtime, phase="execute")
_jobs: ScriptJobs | None = None
_plugins: PluginRuntimes | None = None
_tasks: TaskContainers | None = None
_jobs_lock = threading.Lock()


def jobs() -> ScriptJobs:
    global _jobs
    with _jobs_lock:
        if _jobs is None:
            _jobs = ScriptJobs(DockerEngine())
    return _jobs


def plugins() -> PluginRuntimes:
    global _plugins
    with _jobs_lock:
        if _plugins is None:
            _plugins = PluginRuntimes(DockerEngine())
    return _plugins


def tasks() -> TaskContainers:
    global _tasks
    with _jobs_lock:
        if _tasks is None:
            _tasks = TaskContainers(DockerEngine())
    return _tasks


class ExecuteScriptRequest(BaseModel):
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class ExecuteTaskCallRequest(BaseModel):
    task_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    call_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class CreatePluginRuntimeRequest(BaseModel):
    isolation_key: str = Field(min_length=1, max_length=512)
    tenant_id: str | None = Field(default=None, pattern=TENANT_ID.pattern)


class UpdateSettingsRequest(BaseModel):
    max_pending: int = Field(ge=0, le=100)


class PrepareSkillRequest(BaseModel):
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def _authorize(authorization: str | None) -> None:
    token = os.environ["MOVO_SANDBOX_MANAGER_TOKEN"]
    supplied = (authorization or "").removeprefix("Bearer ")
    if not token or not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health(authorization: str | None = Header(default=None)) -> dict:
    _authorize(authorization)
    try:
        DockerEngine().request("GET", "/_ping")
        docker_ok = True
    except Exception:  # noqa: BLE001
        docker_ok = False
    policy = current_policy()
    result = probe().latest()
    return {"ok": docker_ok and result.ok, "docker_ok": docker_ok, "runtime": policy.runtime, "tenant_mode": policy.tenant_mode,
            "fallback": policy.fallback, "probe_ok": result.ok, "probe": result.as_dict(),
            **jobs().status(), **plugins().status()}


@app.post("/v1/script-jobs")
def run_script(payload: ExecuteScriptRequest, authorization: str | None = Header(default=None)) -> dict:
    _authorize(authorization)
    _require_sandbox("script-job")
    try:
        result = jobs().execute(payload.job_id)
        audit.emit("sandbox.exit", kind="script-job", job=payload.job_id,
                   runtime=current_policy().runtime, exit_code=result.get("exit_code"))
        return result
    except PolicyViolation as exc:
        audit.emit("sandbox.denied", kind="script-job", job=payload.job_id, reason=str(exc))
        raise HTTPException(status_code=503, detail={"code": "sandbox_unavailable", "reason": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DependencyPreparationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@app.post("/v1/script-task-calls")
def run_task_call(payload: ExecuteTaskCallRequest, authorization: str | None = Header(default=None)) -> dict:
    _authorize(authorization)
    _require_sandbox("script-task")
    try:
        result = tasks().execute(payload.task_id, payload.call_id)
        audit.emit("sandbox.exit", kind="script-task", task=payload.task_id, call=payload.call_id,
                   runtime=current_policy().runtime, exit_code=result.get("exit_code"))
        return result
    except PolicyViolation as exc:
        audit.emit("sandbox.denied", kind="script-task", task=payload.task_id, reason=str(exc))
        raise HTTPException(status_code=503, detail={"code": "sandbox_unavailable", "reason": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DependencyPreparationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@app.delete("/v1/script-tasks/{task_id}")
def dispose_task(task_id: str, authorization: str | None = Header(default=None)) -> dict:
    _authorize(authorization)
    try:
        tasks().dispose(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"disposed": True}


@app.post("/v1/script-tasks/{task_id}/freeze")
def freeze_task(task_id: str, authorization: str | None = Header(default=None)) -> dict:
    _authorize(authorization)
    try:
        tasks().freeze(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"frozen": True}


@app.post("/v1/skill-environments", status_code=202)
def prepare_skill(payload: PrepareSkillRequest, authorization: str | None = Header(default=None)) -> dict:
    _authorize(authorization)
    _require_sandbox("skill-build")
    try:
        return {"status": jobs().environments.start(payload.digest, retry_failed=True)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/skill-environments/{digest}")
def skill_environment_status(digest: str, authorization: str | None = Header(default=None)) -> dict:
    _authorize(authorization)
    try:
        return {"status": jobs().environments.status(digest)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/v1/settings")
def update_settings(payload: UpdateSettingsRequest, authorization: str | None = Header(default=None)) -> dict:
    _authorize(authorization)
    return jobs().set_max_pending(payload.max_pending)


@app.post("/v1/plugin-runtimes")
def create_plugin_runtime(payload: CreatePluginRuntimeRequest, authorization: str | None = Header(default=None)) -> dict:
    _authorize(authorization)
    _require_sandbox("plugin-runtime", payload.tenant_id)
    try:
        result = plugins().create(payload.isolation_key, payload.tenant_id)
    except PolicyViolation as exc:
        audit.emit("sandbox.denied", kind="plugin-runtime", tenant=payload.tenant_id, reason=str(exc))
        raise HTTPException(status_code=503, detail={"code": "sandbox_unavailable", "reason": str(exc)}) from exc
    audit.emit("sandbox.create", kind="plugin-runtime", tenant=payload.tenant_id,
               runtime=current_policy().runtime, lease=result.get("lease_id"))
    return result


@app.delete("/v1/plugin-runtimes/{lease_id}")
def dispose_plugin_runtime(lease_id: str, authorization: str | None = Header(default=None)) -> dict:
    _authorize(authorization)
    plugins().dispose(lease_id)
    return {"disposed": True}
