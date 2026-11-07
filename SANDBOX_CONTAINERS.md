# Container isolation (community edition)

MOVO can run governed scripts in one-shot containers and DSH plugins in separate per-profile Runtime containers. The feature is optional during rollout; the existing execution path remains in use until it is enabled. Docker Engine 26 or newer is required for per-job volume subpaths.

## Enable

Add these lines to the deployment `.env` file, then run `./movo up --build` for a source deployment or `./movo update` once the sandbox images have been published:

```dotenv
COMPOSE_PROFILES=sandbox
MOVO_SANDBOX_MANAGER_URL=http://sandbox-manager:8300
MOVO_SANDBOX_MAX_CONCURRENT=2
MOVO_SANDBOX_MAX_PENDING=8
MOVO_SANDBOX_PLUGIN_IDLE_SECONDS=900
```

The manager and egress proxy run as separate Compose services. Only `sock-proxy` mounts the Docker socket; the manager reaches Docker through it (see "Enterprise P0 hardening" below). Script jobs receive a private workspace and internal Docker network. Plugin Runtime containers receive storage scoped to their Runtime isolation key. Network egress is available through the proxy, which rejects private and local destinations. Admin → Settings → Sandbox containers shows current health and counts.

The script concurrency setting limits active one-shot containers. Requests beyond the limit wait up to 20 seconds, with at most `MOVO_SANDBOX_MAX_PENDING` queued. Administrators can adjust the queue limit in Settings; the saved value persists across manager restarts. The concurrency limit remains a deployment setting. A plugin Runtime is reclaimed after the idle timeout; its session storage remains available for the next turn. The first turn after reclamation must start a new container and may take several seconds.

## Packaged Skills

MOVO accepts a Skill ZIP with one `SKILL.md` and up to 20,000 files, 100 MiB compressed, and 200 MiB expanded. A repository ZIP may place the sole Skill in a deeper directory; MOVO keeps only that Skill subtree and stores a canonical ZIP. An invoked Skill's package is cached by its content digest. Each script job mounts that package read-only under `/job/skills` and receives a separate writable `/job` directory. Script code can use `skill_dir` for the exact package root and `output_dir` for files that should be delivered to the user. Packages with dependency manifests are extracted when installed so dependency preparation can begin in the background; packages without manifests are extracted on first execution. Subsequent jobs reuse the cached read-only copy.

The DSH text resource reader caches the compressed archive and extracts only the requested UTF-8 entry. Reading Skill instructions does not unpack thousands of templates into the Runtime Host. This cache is separate from the script execution cache because the two components use different volumes.

The sandbox script base image provides Python, pip, Node.js, npm, and the backend's existing Python libraries. A Skill may declare root-level `requirements.txt`, `package.json` with `package-lock.json`, and `apt-packages.txt` (one Debian package name per line). After installation, MOVO starts an isolated dependency build in the background. The resulting image is reused by Skill archive digest and base-image ID; ordinary script jobs never install packages. The installer shows preparation progress and failure. A call made before preparation finishes waits briefly and then reports that preparation is still in progress. Large dependency sets can take several minutes to prepare, depending on download speed; normal calls after preparation do not repeat installation. `./movo --mirror cn up` and `update` select Chinese package registries for future Skill dependency builds; the default launcher selects official registries. A download timeout or connection failure retries once from the alternate registries in a fresh build container. Package conflicts and install-script failures do not trigger a source switch. Explicit `MOVO_PIP_INDEX_URL`, `MOVO_APT_MIRROR`, and `MOVO_NPM_REGISTRY` values take precedence over the selected primary source. Packages that run install hooks execute only inside the disposable dependency build container, without user files or a Docker socket. Reinstalling the same ZIP retries a failed preparation.

Dependency manifests must be part of the trusted Skill ZIP. Dependencies mentioned only in prose cannot be inferred reliably. Native tools that do not expose a Debian package still require an operator-controlled base image selected with `MOVO_SANDBOX_SCRIPT_IMAGE`.

Do not mount the Docker socket into the chat API, the DSH Host, or the job containers. The manager creates containers from fixed images and commands; requests cannot supply arbitrary Docker configuration.


## Enterprise P0 hardening (Kata, fail-closed)

These rules apply from this release. They implement the P0 design: Kata by default, multi-tenant never falls back to runc, no in-process execution, Docker socket behind a filtering proxy.

### Host prerequisites

- Linux x86_64/arm64 with a usable `/dev/kvm` (bare metal or nested virtualisation; Docker Desktop/WSL are not supported).
- Docker Engine **26.x or 27.x**. Docker 28+ leaves Kata containers without a NIC (moby#52017); the launcher refuses it unless `MOVO_DOCKER_ALLOW_UNTESTED=1`, and the runtime probe must still pass.
- Kata Containers 3.x (QEMU + runtime-rs) registered in `/etc/docker/daemon.json` as `kata` with `runtimeType` + `ConfigPath`. `./movo doctor` prints an example and the current status.

### Configuration contract

| Variable | Default | Rule |
|---|---|---|
| `MOVO_SANDBOX_RUNTIME` | `kata` | `kata` or `runc`; anything else refuses to start |
| `MOVO_TENANT_MODE` | `single` | `single` / `multi`; `multi` + `runc` refuses to start in the launcher, sandbox-manager and sock-proxy, with no override |
| `MOVO_SANDBOX_ALLOW_RUNC` | unset | must be `i-understand` together with `runc` (single tenant only); every start and execution writes a `sandbox.fallback_runc` audit event |
| `MOVO_SANDBOX_MANAGER_URL` | `http://sandbox-manager:8300` | empty or unreachable means chat-api and dsh-runtime-host refuse to execute code/plugins |
| `MOVO_DOCKER_HOST` | `http://sock-proxy:2375` | the manager's only path to Docker |
| `MOVO_SANDBOX_PROBE_INTERVAL` | `600` | seconds; a failing Kata probe stops the manager from accepting work |
| `MOVO_EGRESS_DEFAULT_POLICY` | `deny` | sandbox egress allows package registries plus `MOVO_EGRESS_ALLOW_DOMAINS` / `egress-policy.json` |

Developer-only switches (never set in production): `MOVO_DEV_INPROCESS_SCRIPTS=1` (chat-api) and `MOVO_DEV_INPROCESS_PLUGINS=1` (dsh-runtime-host).

### What enforces it

- `sandbox-manager/app/sandbox_policy.py`: every container create gets `Runtime`, `CapDrop: [ALL]`, `no-new-privileges`, non-root user, PID/memory/CPU limits and `movo.*` labels. Skill dependency builds may keep a fixed capability set only under Kata.
- `sandbox-manager/app/probe.py`: starts a probe container and checks it has `eth0`, can reach `sandbox-egress:3128`, and (for Kata) runs a different kernel from the host. `/health` reports `runtime`, `fallback` and `probe_ok`. Execution endpoints return `503 sandbox_unavailable` while the probe fails.
- `services/sock-proxy`: allowlisted Docker API paths. Container create bodies are validated default-deny: correct runtime; no privileges, CapAdd, devices, binds or host namespaces; volume mounts only on sandbox volumes with a safe Subpath; never the secrets volume; image prefix allowlist. Operations on existing objects require movo labels.
- `sandbox-egress`: default-deny domain allowlist. Private, link-local and metadata addresses stay blocked after DNS resolution.
- `dsh-runtime-host` is on the backend network only and uses `sandbox-egress` as its proxy.
- sandbox-manager runs as UID 10001 and reads only `/run/askai-secrets/sandbox.env`.

### Verified only on a KVM host

The box used for development has no KVM, so these must be checked on the target host: the Kata probe (`./movo doctor`, `/health.probe_ok`), `docker inspect` showing `Runtime=kata` for all sandbox containers, Subpath behaviour over virtio-fs, plugin runtimes and Skill builds under Kata, and the Docker 26/27 networking matrix.
