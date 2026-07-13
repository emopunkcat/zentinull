#!/usr/bin/env bash
#
# SSH tunnel helper for Zentinull remote ingest.
#
# Runs SSH port forwarding to a machine on the IT network, then executes
# serve.py pipeline (or any command) with env vars pointing at local tunnels.
#
# Usage:
#   # Full pipeline via SSH tunnel:
#   ./scripts/tunnel.sh user@jump-box serve.py pipeline
#
#   # Just ingest:
#   ./scripts/tunnel.sh user@jump-box serve.py ingest
#
#   # Custom ports / different SSH host:
#   ./scripts/tunnel.sh -p 2222 user@jump-box serve.py start
#
# The SSH host must have network access to the 6 IT sources.
# Port mappings (all default ports can be overridden via env vars):
#
#   Source          Local Port    Remote Target
#   ─────────────────────────────────────────────────
#   SharePoint      15678         192.168.20.56:5678
#   FortiGate       14443         192.168.20.1:443
#   ManageEngine    18080         endpointcentral.manageengine.com:443
#   ManageEngine MDM 18081        mdm.manageengine.com:443
#   ServiceDesk+    18082         sdpondemand.manageengine.com:443
#   Active Directory  1389        192.168.20.11:389
#   Zabbix          18083         zabbix.example.com:443
#

set -euo pipefail

# ── Load SSH password from .env if present ────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
if [ -f "$ROOT_DIR/.env" ] && [ -z "${SSH_PASS:-}" ]; then
    SSH_PASS="$(python3 -c "
import os
env_file = '$ROOT_DIR/.env'
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line.startswith('SSH_PASS=') or line.startswith('SSH_PASS '):
                val = line.split('=', 1)[1].strip()
                # Strip surrounding quotes
                if (val.startswith(\"'\") and val.endswith(\"'\")) or \
                   (val.startswith('\"') and val.endswith('\"')):
                    val = val[1:-1]
                print(val)
                break
" 2>/dev/null || true)"
fi
# ── Default port mappings ─────────────────────────────────────────────────────
# Each LOCAL_PORT:REMOTE_HOST:REMOTE_PORT
# Override any via env vars, e.g. SHAREPOINT_LOCAL=9999
SHAREPOINT_FWD="${SHAREPOINT_LOCAL:-15678}:${SHAREPOINT_REMOTE:-192.168.20.56:5678}"
FORTIGATE_FWD="${FORTIGATE_LOCAL:-14443}:${FORTIGATE_REMOTE:-192.168.20.1:443}"
ME_CLOUD_FWD="${ME_CLOUD_LOCAL:-18080}:${ME_CLOUD_REMOTE:-endpointcentral.manageengine.com:443}"
ME_MDM_FWD="${ME_MDM_LOCAL:-18081}:${ME_MDM_REMOTE:-mdm.manageengine.com:443}"
SDP_FWD="${SDP_LOCAL:-18082}:${SDP_REMOTE:-sdpondemand.manageengine.com:443}"
AD_FWD="${AD_LOCAL:-1389}:${AD_REMOTE:-192.168.20.11:389}"
ZBX_FWD="${ZBX_LOCAL:-18083}:${ZBX_REMOTE:-zabbix.example.com:443}"

# ── Parse args ────────────────────────────────────────────────────────────────
SSH_OPTS=""
while getopts "p:o:" opt; do
    case "$opt" in
        p) SSH_OPTS="$SSH_OPTS -p $OPTARG" ;;
        o) SSH_OPTS="$SSH_OPTS $OPTARG" ;;
        *) echo "Usage: $0 [-p port] [-o ssh-opt] user@host [command...]" >&2; exit 1 ;;
    esac
done
shift $((OPTIND-1))

if [ $# -lt 1 ]; then
    echo "Usage: $0 [-p port] [-o ssh-opt] user@host [command...]" >&2
    exit 1
fi

SSH_HOST="$1"
shift

# Remaining args = command to run (default: python3 serve.py pipeline)
CMD=("$@")
if [ ${#CMD[@]} -eq 0 ]; then
    CMD=("python3" "serve.py" "pipeline")
elif [ "${CMD[0]}" = "serve.py" ]; then
    CMD=("python3" "${CMD[@]}")
fi

# ── Build SSH -L flags ────────────────────────────────────────────────────────
L_FLAGS=()
_add_fwd() {
    local name="$1" local_port="$2" target="$3"
    L_FLAGS+=(-L "${local_port}:${target}")
    echo "  tunnel  ${name}:  localhost:${local_port%%:*} → ${target}"
}

echo "═ Zentinull SSH Tunnel ═"
echo "  jump:  ${SSH_HOST}"
echo

_add_fwd "SharePoint"      "$(echo "$SHAREPOINT_FWD" | cut -d: -f1)" "$(echo "$SHAREPOINT_FWD" | cut -d: -f2- )"
_add_fwd "FortiGate"       "$(echo "$FORTIGATE_FWD"   | cut -d: -f1)" "$(echo "$FORTIGATE_FWD"   | cut -d: -f2- )"
_add_fwd "ME Cloud"        "$(echo "$ME_CLOUD_FWD"    | cut -d: -f1)" "$(echo "$ME_CLOUD_FWD"    | cut -d: -f2- )"
_add_fwd "ME MDM"          "$(echo "$ME_MDM_FWD"      | cut -d: -f1)" "$(echo "$ME_MDM_FWD"      | cut -d: -f2- )"
_add_fwd "ServiceDesk+"    "$(echo "$SDP_FWD"         | cut -d: -f1)" "$(echo "$SDP_FWD"         | cut -d: -f2- )"
_add_fwd "AD LDAP"         "$(echo "$AD_FWD"          | cut -d: -f1)" "$(echo "$AD_FWD"          | cut -d: -f2- )"
_add_fwd "Zabbix"          "$(echo "$ZBX_FWD"         | cut -d: -f1)" "$(echo "$ZBX_FWD"         | cut -d: -f2- )"

echo
echo "  command: ${CMD[*]}"
echo

# ── Set env vars for local forwarding ─────────────────────────────────────────
export SHAREPOINT_BASE_URL="http://localhost:$(echo "$SHAREPOINT_FWD" | cut -d: -f1)/webhook"
export FG_HOST="localhost"
export FG_PORT="$(echo "$FORTIGATE_FWD" | cut -d: -f1)"
export ME_CLOUD_BASE_URL="https://localhost:$(echo "$ME_CLOUD_FWD" | cut -d: -f1)/api/1.4"
export ME_MDM_BASE_URL="https://localhost:$(echo "$ME_MDM_FWD" | cut -d: -f1)/api/v1/mdm"
export SDP_BASE_URL="https://localhost:$(echo "$SDP_FWD" | cut -d: -f1)"
export AD_SERVER="ldap://localhost:$(echo "$AD_FWD" | cut -d: -f1)"
export ZBX_URL="https://localhost:$(echo "$ZBX_FWD" | cut -d: -f1)/api_jsonrpc.php"

# ── Start SSH tunnel in background ────────────────────────────────────────────
echo "Starting SSH tunnel (background)..."
SSH_CMD=(ssh)
if [ -n "${SSH_PASS:-}" ]; then
    export SSHPASS="$SSH_PASS"
    SSH_CMD=(sshpass -e ssh)
fi
# Accept new host keys silently (needed for first connection to jump box)
SSH_EXTRA=(-o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes)
"${SSH_CMD[@]}" -N "${L_FLAGS[@]}" "${SSH_EXTRA[@]}" $SSH_OPTS "$SSH_HOST" &
SSH_PID=$!
# Give the tunnel a moment to establish
sleep 1
if ! kill -0 "$SSH_PID" 2>/dev/null; then
    echo "ERROR: SSH tunnel failed to start. Is the jump box reachable?"
    exit 1
fi

# ── Cleanup on exit ───────────────────────────────────────────────────────────
cleanup() {
    echo
    if [ -n "$SSH_PID" ]; then
        echo "Closing SSH tunnel..."
        kill -- -"$SSH_PID" 2>/dev/null || true
        wait "$SSH_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# ── Run the command ───────────────────────────────────────────────────────────
set +e
"${CMD[@]}"
EXIT_CODE=$?
set -e

echo
echo "Command exited with code ${EXIT_CODE}"

exit "$EXIT_CODE"
