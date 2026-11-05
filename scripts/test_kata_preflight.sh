#!/usr/bin/env bash
# Unit tests for deploy/cli/kata.sh using a fake docker binary (no KVM needed).
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "${tmp}"' EXIT
cat > "${tmp}/docker" <<'FAKE'
#!/usr/bin/env bash
case "$*" in
  "version --format {{.Server.Version}}") echo "${FAKE_DOCKER_VERSION}" ;;
  "info --format {{json .Runtimes}}") echo "${FAKE_RUNTIMES}" ;;
esac
FAKE
chmod +x "${tmp}/docker"
: > "${tmp}/kvm"
dotenv_value() { :; }
movo_msg() { printf '%s\n' "$1"; }
DOCKER_BIN="${tmp}/docker"
# shellcheck source=deploy/cli/kata.sh
source "${ROOT_DIR}/deploy/cli/kata.sh"
fail=0
expect() { # expect <0|2> <label> <cmd...>
  local want="$1" label="$2"; shift 2
  local got=0; "$@" >/dev/null 2>&1 || got=$?
  if [[ "${got}" != "${want}" ]]; then printf 'FAIL %s: want %s got %s\n' "${label}" "${want}" "${got}"; fail=1
  else printf 'ok   %s\n' "${label}"; fi
}
run_policy() { ( MOVO_SANDBOX_RUNTIME="$1" MOVO_TENANT_MODE="$2" MOVO_SANDBOX_ALLOW_RUNC="$3"; movo_sandbox_policy_check ); }
expect 0 "default kata/single"       run_policy "" "" ""
expect 2 "multi + runc forbidden"     run_policy runc multi i-understand
expect 2 "runc without ack"           run_policy runc single ""
expect 0 "runc single with ack"       run_policy runc single i-understand
expect 2 "invalid runtime"            run_policy runsc single ""
expect 2 "invalid tenant mode"        run_policy kata both ""
for v in 25.0.5 28.0.1 29.1.0 garbage; do expect 1 "docker ${v} unsupported" movo_docker_version_supported "${v}"; done
for v in 26.1.4 27.5.1; do expect 0 "docker ${v} supported" movo_docker_version_supported "${v}"; done
host() { ( FAKE_DOCKER_VERSION="$1" FAKE_RUNTIMES="$2" MOVO_KVM_DEVICE="$3" MOVO_SANDBOX_RUNTIME="${4:-kata}" \
          MOVO_DOCKER_ALLOW_UNTESTED="${5:-}"; export FAKE_DOCKER_VERSION FAKE_RUNTIMES; movo_kata_host_check ); }
expect 2 "no /dev/kvm"                host 27.5.1 '{"kata":{},"runc":{}}' /nonexistent
expect 2 "kata not registered"        host 27.5.1 '{"runc":{}}' /nonexistent
expect 2 "docker 28 refused"          host 28.0.1 '{"kata":{}}' /nonexistent
expect 0 "runc fallback skips kvm"    host 27.5.1 '{"runc":{}}' /nonexistent runc
expect 2 "docker 25 even if untested" host 25.0.0 '{"runc":{}}' /nonexistent runc 1
expect 0 "docker 28 with untested=1"  host 28.0.1 '{"runc":{}}' /nonexistent runc 1
exit "${fail}"
