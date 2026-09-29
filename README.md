# Mimir

Per-process resource monitor for Windows 11 and Linux. It keeps history and peaks.
It is like Task Manager's *Processes* tab, but every value is charted over time,
and Mimir remembers the peak of every metric and the time it occurred.

Tracked per process: CPU, memory, disk read/write, network download/upload,
GPU utilisation and VRAM, thread count.
Tracked system-wide: CPU, memory, disk, network, GPU utilisation and VRAM.

Mimir is written in C++20 with Dear ImGui and ImPlot on GLFW + OpenGL. One
executable, no runtime dependencies.

## Build and run

Linux (needs cmake, a C++20 compiler, and the X11 and OpenGL headers):

```sh
./build.sh           # Release build into bin/, runs the tests, sets capabilities (asks for sudo)
bin/mimir
```

`build.sh` gives `bin/mimir` three capabilities: `cap_net_raw` (copy packets),
and `cap_dac_read_search` + `cap_sys_ptrace` (read other users' `/proc/<pid>/fd`
and `/proc/<pid>/io`, so the traffic and disk I/O of root-owned processes such
as openvpn are attributed too). Any code that bin/mimir runs can then read every
file and process on the machine. `./build.sh --no-caps` skips that step;
everything except per-process network then still works. A rebuild drops the
capabilities, so run `build.sh` again after a change.

Windows (needs the Visual Studio 2022 Build Tools with the C++ workload):

```bat
build.bat            :: Release build into bin\, runs the tests
bin\mimir.exe
run_admin.bat        :: start elevated: per-process network needs administrator rights
```

The first build downloads the pinned sources of GLFW, Dear ImGui, ImPlot and stb.

Options: `--interval SECONDS`, `--no-network`, `--log-level debug|info|warn|error`,
`--shot SECONDS --shot-file FILE` (save a screenshot and exit), `--version`.

## Using it

- **Tiles** at the top show live system totals, a 60 s sparkline and the
  all-time peak. Click a tile to chart that resource. The scope stays the same.
- **Process table**: click a header to sort. Click a row to chart that process
  (or that group of same-named processes); click it again to go back.
  Right-click a row to end it: after a confirmation, the process, its children
  (by default) and every instance in a grouped row are terminated. Protected
  system processes cannot be ended from user mode, and Mimir reports them.
  Type in the filter box to search.
  "Group by name" merges every `chrome.exe` into one row and adds up its values.
  Peak columns cover the time since the start (or since *Clear history*).
- **Chart**: pick a resource (CPU, Memory, Disk, Network, GPU) and a scope
  (System, Top processes, Selected). The peak in the visible window is marked.
  GPU gets two stacked charts, utilisation and VRAM. In Top processes each chart
  ranks its own leaders, and a process keeps its colour across both.
  Right-click a legend entry to end that process.
- Drag the gap between the table and the chart to resize them.
- **Export CSV** writes the full history of the visible scope to `exports/`.
- **Settings** sets the sampling interval, kept history, ranking window, chart
  span and more. Mimir saves them to `settings.json` on exit.

## Resource use

The window draws only when something changes: on input, and once per sample.
While the mouse or keyboard is in use, it draws up to every 0.1 s, so hover
states and tooltips keep up. A minimized window does not draw.

Measured on a laptop with 380 processes, default settings, against the last
Python version:

| | Python | C++ |
|---|---|---|
| CPU, network capture off | 13-24% of a core | 3.3-4.4% |
| CPU, capture on, 2,500 packets/s | 87-107% | 3.9-7.8% |
| Memory (RSS) at start | 142 MB | 101 MB |
| History per process per sample | 98 B | 17 B |

Most of the RSS is shared driver code. An empty OpenGL window (glxgears) takes
69 MB on the same laptop, and the NVIDIA library (NVML) adds 18 MB. Mimir's own
part is about 15 MB, plus about 11 MB for 30 minutes of history. On a Windows 11
desktop Mimir used 0.2% of a core.

## How processes are read

- **Windows:** every sample is one `NtQuerySystemInformation` call, the API
  Task Manager uses. It returns CPU times, private working set, I/O counters and
  thread counts for all processes at once, with no per-process handles, so
  protected processes are included.
- **Linux:** two reads per process, `/proc/<pid>/stat` and `/proc/<pid>/io`.
  This is the cost floor of the kernel interface: about 7 ms for 375 processes.
  Names longer than 15 characters come from `cmdline`, as psutil does it, and
  are cached per process.

System totals count physical disks and NICs only. Device-mapper disks, VPN
tunnels, bridges and loopback repeat traffic that a physical device also carries.

## GPU figures

- **Windows:** per-process utilisation and dedicated VRAM come from the
  performance counters `GPU Engine` and `GPU Process Memory`, the data behind
  Task Manager's GPU columns. They work for NVIDIA, AMD and Intel and need no
  admin rights.
- **NVML (NVIDIA only):** Mimir loads the driver's library at run time, so no
  SDK is needed. It supplies the whole-GPU utilisation, VRAM and the adapter
  name. On Linux it also supplies the per-process figures.

A process's "GPU" is its busiest engine, as in Task Manager.

## Network attribution and why it is safe

Neither backend sits in the traffic path. A slow or crashed Mimir cannot delay
or drop traffic.

- **Linux:** an `AF_PACKET` ring. The kernel copies the first 128 bytes of each
  packet into shared memory and wakes Mimir when a block is full or 100 ms old,
  so there is no system call per packet. A kernel filter drops loopback copies.
  When Mimir falls behind, the ring drops copies, never traffic. Mimir matches
  each packet to a process through the socket table. It reads
  `/proc/net/{tcp,udp}{,6}` once per second and scans `/proc/*/fd` only when
  traffic flows on a socket whose owner it does not know. On a VPN, application traffic is attributed on the tunnel
  and the encrypted outer traffic to the VPN client.
- **Windows:** an ETW session on the `Microsoft-Windows-Kernel-Network`
  provider. The kernel reports the bytes sent and received per process, the data
  Resource Monitor shows. There is no packet driver and no socket table.
  Loopback is skipped.

## Layout

```
src/main.cpp          command line, wiring
src/metrics.h         metric table, chart groups, value formatting
src/history.*         thread-safe tick store, peaks, rankings, series queries
src/settings.*        settings.json
src/log.*             log to stderr and logs/mimir.log
src/sampler.*         background sampling thread
src/sys.h             OS interface: processes, system counters, kill, rights
src/sys_linux.cpp     ... on /proc and /sys
src/sys_windows.cpp   ... on NtQuerySystemInformation and Win32
src/gpu.*             NVML, and PDH counters on Windows
src/net.h             per-process network interface
src/net_linux.cpp     AF_PACKET ring capture
src/net_windows.cpp   ETW capture
src/ui.*              the window
src/theme.*           colours, style, fonts
tests/tests.cpp       unit tests (bin/mimir_tests), --dump prints one process snapshot
assets/               icon and logo
```

Design rules: the sampler never touches the UI. The UI reads the store on its
own thread and rebuilds its view only when a sample or a setting changes.
Everything OS-specific lives behind `sys.h` and `net.h`.
