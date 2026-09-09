# Mimir

Per-process resource monitor for Windows 11 (Linux planned) that keeps history
and peaks. Think Task Manager's *Processes* tab, but every value is charted over
time and the peak of every metric is remembered with the moment it happened.

Tracked per process: CPU, memory (private working set), disk read/write,
network download/upload (admin only), GPU utilisation (NVIDIA), thread count.
Tracked system-wide: CPU, memory, disk, network, GPU utilisation and VRAM.

## Run

```bat
setup.bat        :: once - creates venv\ and installs requirements
run.bat          :: start Mimir
run_admin.bat    :: start elevated - enables per-process network stats
```

Or `venv\Scripts\python.exe -m mimir`. `--help` lists options
(`--interval`, `--no-network`, `--log-level`, `--shot`).

## Using it

- **Tiles** at the top show live system totals with a 60 s sparkline and the
  all-time peak. Click a tile to chart that resource for the whole system.
- **Process table**: click a header to sort, click a row to chart that process
  (or that group of same-named processes). Right-click a row to end it: after
  a confirmation the process, its children by default, and every instance in a
  grouped row are terminated with TerminateProcess (SeDebugPrivilege is used
  when elevated, `taskkill /F /T` is the fallback). Protected system processes
  cannot be ended from user mode and are reported. Type in the filter box to
  search.
  "Group by name" merges every `chrome.exe` into one row and sums its values.
  Peak columns are since the app started (or since *Clear history*).
- **Chart**: pick a resource (CPU, Memory, Disk, Network, GPU) and a scope
  (System, Top processes, Selected). The time axis is wall-clock time; the peak
  inside the visible window is annotated. Resources with two units (GPU shows
  utilisation and VRAM) get two stacked charts; in Top processes each chart
  ranks its own leaders, with one colour per process across both. A tile
  changes only the resource, the scope is kept.
- **Export CSV** writes the visible scope's full history to `exports\`.
- **Settings** (gear button) adjusts the sampling interval, retained history,
  ranking window, chart span and so on. Saved to `settings.json` on exit.

## How processes are read

On Windows every sample is one `NtQuerySystemInformation` call, the same API
Task Manager uses. It returns CPU times, private working set, I/O counters and
thread counts for all processes at once in a few milliseconds, with no
per-process handles, so protected processes are included. psutil is the
fallback on other platforms (and needs about 100x longer per sample here).

## GPU figures

Per-process GPU utilisation and dedicated VRAM come from the Windows
performance counters `GPU Engine` and `GPU Process Memory`, the data behind
Task Manager's GPU columns. They work for NVIDIA, AMD and Intel, need no admin
rights, and cost under a millisecond per sample. NVML (NVIDIA only) supplies
the whole-GPU utilisation, VRAM used/total and the adapter name; on Linux it
also supplies the per-process figures, since WDDM hides those from NVML on
Windows. A process's "GPU" is its busiest engine, as in Task Manager.

## Network attribution and why it is safe

psutil cannot say which process a packet belongs to, so Mimir (only when run
as administrator) opens the WinDivert driver in **SNIFF** mode. In that mode
the driver hands Mimir a *copy* of each packet; the real packet is never
queued, delayed, or re-injected by Mimir, so a slow or crashed Mimir cannot
affect traffic. Each packet is matched to its owning PID through the socket
table from `psutil.net_connections()`.

The previous version opened WinDivert in *divert* mode, which pulled every
packet on the machine into Python and required Mimir to send it back out. Any
stall in the Python loop then delayed or dropped real traffic, which is what
made the connection collapse. That code path no longer exists.

## Layout

```
mimir/
  __main__.py      CLI entry, logging, wiring
  settings.py      dataclass settings, JSON persistence
  metrics.py       metric definitions, formatting
  history.py       thread-safe snapshot store, peaks, rankings, series queries
  sampler.py       background psutil/NVML sampling thread
  netcapture.py    passive per-process network attribution (WinDivert sniff)
  winproc.py       one-call process snapshot via NtQuerySystemInformation (Windows)
  gpu.py           optional NVML wrapper
  platform.py      OS specifics: admin check, elevation, font paths
  ui/theme.py      colours, fonts, DearPyGui themes
  ui/app.py        the window
assets/          window icon (mimir.ico) and logo (mimir.png)
tools/make_icon.py  regenerates the icon files (needs Pillow, dev only)
tests/             pytest unit tests for the store, metrics and settings
```

Design rules: the sampler never touches the UI; the UI only reads the store on
the render thread; anything OS-specific lives in `platform.py` or behind the
`NetworkAttributor` interface so a Linux backend can be added without touching
the rest.

## Development

```bat
venv\Scripts\python.exe -m pip install -e .[dev]
venv\Scripts\python.exe -m pytest
venv\Scripts\python.exe -m mimir --shot 8 --shot-file shot.png   :: headless screenshot
```
