#!/usr/bin/env bash
set -Eeuo pipefail

PORT=8000
VENV_DIR=/tmp/kravel-laya-venv
PID_FILE=/tmp/kravel-laya.pid
LOG_FILE=/tmp/kravel-laya.log

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

stop_server() {
  local pid=""
  if [[ -r "${PID_FILE}" ]]; then
    pid=$(<"${PID_FILE}")
  fi
  if [[ "${pid}" =~ ^[0-9]+$ && -r "/proc/${pid}/cmdline" ]]; then
    if tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -Fq -- "${VENV_DIR}/bin/laya-serve"; then
      kill "${pid}" 2>/dev/null || true
    fi
  fi
  rm -f "${PID_FILE}"
}

[[ "${CODESPACES:-}" == "true" && -n "${CODESPACE_NAME:-}" ]] \
  || fail "Run this script inside a GitHub Codespace for the Kravel repository"

if [[ "${1:-start}" == "stop" ]]; then
  if command -v gh >/dev/null 2>&1; then
    gh codespace ports visibility "${PORT}:private" -c "${CODESPACE_NAME}" >/dev/null 2>&1 || true
  fi
  stop_server
  printf 'Stopped the temporary Laya server and restored private port visibility when GitHub CLI allowed it.\n'
  exit 0
fi
[[ "${1:-start}" == "start" ]] || fail "usage: bash demo/codespaces/laya-server.sh [start|stop]"

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"

printf '\n==> Installing the pinned Laya server in an isolated temporary environment\n'
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --quiet --disable-pip-version-check "laya[serve]==0.3.20"

stop_server
LAYA_TOKEN=$("${VENV_DIR}/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')
export LAYA_HOST=127.0.0.1
export LAYA_PORT="${PORT}"
export LAYA_DEVICE=cpu
export LAYA_PRELOAD=1
export LAYA_MODELS=english
export LAYA_THREADS=2
export LAYA_API_KEY="${LAYA_TOKEN}"
nohup "${VENV_DIR}/bin/laya-serve" >"${LOG_FILE}" 2>&1 &
LAYA_PID=$!
printf '%s\n' "${LAYA_PID}" >"${PID_FILE}"
unset LAYA_API_KEY

printf '\n==> Loading the English checkpoint (the first run can take several minutes)\n'
deadline=$((SECONDS + 420))
until curl --fail --silent --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; do
  if ! kill -0 "${LAYA_PID}" 2>/dev/null; then
    fail "Laya stopped during startup; inspect ${LOG_FILE}"
  fi
  if [[ "${SECONDS}" -ge "${deadline}" ]]; then
    stop_server
    fail "Laya did not become healthy within seven minutes; inspect ${LOG_FILE}"
  fi
  sleep 2
done

# Printing localhost causes Codespaces to discover and forward the listening port.
printf 'Local health endpoint: http://localhost:%s/health\n' "${PORT}"
sleep 2

PUBLIC_READY=false
if command -v gh >/dev/null 2>&1; then
  if gh codespace ports visibility "${PORT}:public" -c "${CODESPACE_NAME}" >/dev/null 2>&1; then
    PUBLIC_READY=true
  fi
fi

FORWARDING_DOMAIN=${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}
PUBLIC_URL="https://${CODESPACE_NAME}-${PORT}.${FORWARDING_DOMAIN}/v1/systemone"
printf '\nLaya is ready. Enter these values only when compare-flows.sh prompts for them:\n'
printf 'URL:   %s\n' "${PUBLIC_URL}"
printf 'Token: %s\n' "${LAYA_TOKEN}"
if [[ "${PUBLIC_READY}" != "true" ]]; then
  printf '\nBefore running the comparison, open the Codespace PORTS tab and set port %s visibility to Public.\n' "${PORT}"
fi
cat <<'EOF'

Keep this Codespace running during the demo. Do not save or share this output.
When finished, run: bash demo/codespaces/laya-server.sh stop
Then stop or delete the Codespace so it no longer consumes included usage.
EOF
