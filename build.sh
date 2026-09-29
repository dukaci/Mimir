#!/usr/bin/env bash
# Builds Mimir (Release) into bin/, then grants bin/mimir the capabilities
# per-process network monitoring needs. ./build.sh --no-caps skips that step.
set -euo pipefail
cd "$(dirname "$0")"
generator=()
command -v ninja >/dev/null && generator=(-G Ninja)
[[ -f build/CMakeCache.txt ]] || cmake -S . -B build "${generator[@]}" -DCMAKE_BUILD_TYPE=Release
cmake --build build
bin/mimir_tests

# CAP_NET_RAW: copy packets. CAP_DAC_READ_SEARCH + CAP_SYS_PTRACE: read other users'
# /proc/<pid>/fd and /proc/<pid>/io, so the traffic and disk I/O of root-owned processes
# (openvpn, tailscaled) are attributed too. They apply to bin/mimir only; a rebuild drops them.
if [[ "${1:-}" != "--no-caps" ]]; then
    sudo setcap cap_net_raw,cap_dac_read_search,cap_sys_ptrace+ep bin/mimir
    getcap bin/mimir
fi
echo "Done. bin/mimir starts Mimir."
