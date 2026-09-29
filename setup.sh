#!/usr/bin/env bash
# Mimir setup for Linux: a venv with its own copy of python, the dependencies,
# and the capabilities per-process network monitoring needs.
set -euo pipefail
cd "$(dirname "$0")"

# --copies: the capabilities below go on this venv's python only, never the system one
python3 -m venv --copies venv
venv/bin/python3 -m pip install -q --upgrade pip
venv/bin/python3 -m pip install -q -r requirements.txt
venv/bin/python3 -c "import psutil, dearpygui.dearpygui; print('ok')"

# CAP_NET_RAW: copy packets. CAP_DAC_READ_SEARCH + CAP_SYS_PTRACE: read other users'
# /proc/<pid>/fd, to see which process owns each socket (openvpn, tailscaled, ...).
# Any code this interpreter runs can then read every file and process on the machine.
# Skip with --no-caps: everything but per-process network still works.
if [[ "${1:-}" != "--no-caps" ]]; then
    sudo setcap cap_net_raw,cap_dac_read_search,cap_sys_ptrace+ep venv/bin/python3
    getcap venv/bin/python3
fi

echo "Done. ./run.sh starts Mimir."
