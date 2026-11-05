#!/usr/bin/env bash
# P0 Kata host checks shared by `./movo up` preflight and `./movo doctor`.
# Nothing here modifies the host; it only inspects and prints guidance.

MOVO_DOCKER_MIN_MAJOR=26
MOVO_DOCKER_MAX_MAJOR_EXCLUSIVE=28   # Docker 28+: Kata containers get no NIC (moby#52017)

# movo_docker_version_supported <version> -> 0 if 26.x/27.x
movo_docker_version_supported() {
  local major="${1%%.*}"
  [[ "${major}" =~ ^[0-9]+$ ]] || return 1
  (( major >= MOVO_DOCKER_MIN_MAJOR && major < MOVO_DOCKER_MAX_MAJOR_EXCLUSIVE ))
}

movo_kvm_available() {
  [[ -c "${MOVO_KVM_DEVICE:-/dev/kvm}" && -r "${MOVO_KVM_DEVICE:-/dev/kvm}" && -w "${MOVO_KVM_DEVICE:-/dev/kvm}" ]]
}

# movo_runtime_registered <runtimes-json> <name>
movo_runtime_registered() {
  local runtimes="$1" name="$2"
  [[ "${runtimes}" == *"\"${name}\""* ]]
}

# Validates the configuration contract. Returns 2 on invalid/forbidden combos.
movo_sandbox_policy_check() {
  local runtime mode allow
  runtime="${MOVO_SANDBOX_RUNTIME:-$(dotenv_value MOVO_SANDBOX_RUNTIME)}"
  mode="${MOVO_TENANT_MODE:-$(dotenv_value MOVO_TENANT_MODE)}"
  allow="${MOVO_SANDBOX_ALLOW_RUNC:-$(dotenv_value MOVO_SANDBOX_ALLOW_RUNC)}"
  runtime="${runtime:-kata}"
  mode="${mode:-single}"
  case "${runtime}" in kata|runc) ;; *) movo_msg sandbox_runtime_invalid "${runtime}" >&2; return 2 ;; esac
  case "${mode}" in single|multi) ;; *) movo_msg sandbox_tenant_invalid "${mode}" >&2; return 2 ;; esac
  if [[ "${runtime}" == runc && "${mode}" == multi ]]; then
    movo_msg sandbox_multi_runc >&2
    return 2
  fi
  if [[ "${runtime}" == runc && "${allow}" != i-understand ]]; then
    movo_msg sandbox_runc_ack >&2
    return 2
  fi
  MOVO_SANDBOX_RUNTIME="${runtime}"
  MOVO_TENANT_MODE="${mode}"
  export MOVO_SANDBOX_RUNTIME MOVO_TENANT_MODE
}

# Host checks for the selected runtime. Echoes nothing on success.
movo_kata_host_check() {
  local engine_version runtimes
  engine_version="$("${DOCKER_BIN}" version --format '{{.Server.Version}}' 2>/dev/null || true)"
  if ! movo_docker_version_supported "${engine_version}"; then
    if [[ "${MOVO_DOCKER_ALLOW_UNTESTED:-}" == 1 && "${engine_version%%.*}" -ge 26 ]] 2>/dev/null; then
      movo_msg sandbox_engine_untested "${engine_version}" >&2
    else
      movo_msg sandbox_engine_range "${engine_version:-unknown}" >&2
      return 2
    fi
  fi
  [[ "${MOVO_SANDBOX_RUNTIME:-kata}" == kata ]] || return 0
  if ! movo_kvm_available; then
    movo_msg sandbox_no_kvm >&2
    return 2
  fi
  runtimes="$("${DOCKER_BIN}" info --format '{{json .Runtimes}}' 2>/dev/null || true)"
  if ! movo_runtime_registered "${runtimes}" kata; then
    movo_msg sandbox_no_kata >&2
    return 2
  fi
}

movo_doctor() {
  local json=false engine_version runtimes kvm=false kata=false version_ok=false health="" policy_ok=true
  [[ "${1:-}" == --json ]] && json=true
  movo_sandbox_policy_check 2>/dev/null || policy_ok=false
  engine_version="$("${DOCKER_BIN}" version --format '{{.Server.Version}}' 2>/dev/null || true)"
  movo_docker_version_supported "${engine_version}" && version_ok=true
  movo_kvm_available && kvm=true
  runtimes="$("${DOCKER_BIN}" info --format '{{json .Runtimes}}' 2>/dev/null || true)"
  movo_runtime_registered "${runtimes}" kata && kata=true
  health="$(movo_compose exec -T sandbox-manager sh -c \
    '. /run/askai-secrets/sandbox.env; python -c "import os,urllib.request;print(urllib.request.urlopen(urllib.request.Request(\"http://127.0.0.1:8300/health\",headers={\"Authorization\":\"Bearer \"+os.environ[\"MOVO_SANDBOX_MANAGER_TOKEN\"]}),timeout=5).read().decode())"' \
    2>/dev/null || true)"
  if [[ "${json}" == true ]]; then
    printf '{"docker_version":"%s","docker_version_ok":%s,"kvm":%s,"kata_registered":%s,"runtime":"%s","tenant_mode":"%s","policy_ok":%s,"manager_health":%s}\n' \
      "${engine_version}" "${version_ok}" "${kvm}" "${kata}" "${MOVO_SANDBOX_RUNTIME:-kata}" "${MOVO_TENANT_MODE:-single}" \
      "${policy_ok}" "${health:-null}"
    return 0
  fi
  printf 'Docker Engine      : %s (supported: 26.x/27.x) -> %s\n' "${engine_version:-unknown}" "${version_ok}"
  printf '/dev/kvm           : %s\n' "${kvm}"
  printf 'Kata runtime       : %s\n' "${kata}"
  printf 'Sandbox runtime    : %s   tenant mode: %s   policy ok: %s\n' "${MOVO_SANDBOX_RUNTIME:-kata}" "${MOVO_TENANT_MODE:-single}" "${policy_ok}"
  printf 'Manager /health    : %s\n' "${health:-unreachable}"
  if [[ "${kata}" != true ]]; then
    cat <<'HINT'

Register Kata (QEMU + runtime-rs) in /etc/docker/daemon.json, then restart Docker:
  {
    "runtimes": {
      "kata": {
        "runtimeType": "io.containerd.kata.v2",
        "options": { "ConfigPath": "/opt/kata/share/defaults/kata-containers/runtime-rs/configuration-qemu-runtime-rs.toml" }
      }
    }
  }
HINT
  fi
}
