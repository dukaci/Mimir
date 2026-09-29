"""Fast whole-system process snapshot on Linux.

Two reads per process, /proc/<pid>/stat (CPU time, threads, resident memory)
and /proc/<pid>/io (disk bytes), give the same kernel counters psutil reads.
psutil opens about five files per process and wraps each in Python objects,
which makes it about six times slower for the same numbers.
"""

from __future__ import annotations

import logging
import os

from .winproc import ProcInfo

log = logging.getLogger(__name__)

_PAGE = os.sysconf("SC_PAGE_SIZE")
_HZ = os.sysconf("SC_CLK_TCK")
_COMM_MAX = 15          # the kernel cuts comm to this many bytes


def _read(path: str) -> bytes:
    """File contents, or b"" when the process is gone or access is denied."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return b""
    try:
        return os.read(fd, 4096)
    except OSError:
        return b""
    finally:
        os.close(fd)


def _boot_time() -> float:
    with open("/proc/stat", "rb") as f:         # btime comes after the long intr line, past 4 KB
        for line in f:
            if line.startswith(b"btime"):
                return float(line.split()[1])
    return 0.0


def _full_name(pid: str, comm: str) -> str:
    """comm is cut to 15 bytes; like psutil, take the name from cmdline when it starts the same."""
    data = _read(f"/proc/{pid}/cmdline").decode(errors="replace")
    if not data:
        return comm
    # psutil's rule: args end in NUL, but setproctitle() users (Chrome) separate them with spaces
    sep = "\0" if data.endswith("\0") else " "
    data = data.removesuffix(sep)
    args = data.split(sep)
    if sep == "\0" and len(args) == 1 and " " in data:
        args = data.split(" ")
    exe = os.path.basename(args[0])
    return exe if exe.startswith(comm) else comm


class LinuxProcessSnapshot:
    """Callable that returns {pid: ProcInfo} for every process."""

    def __init__(self):
        self._boot = _boot_time()
        self._names: dict[int, tuple[bytes, str]] = {}      # pid -> (stat up to comm, name)

    def __call__(self) -> dict[int, ProcInfo]:
        out: dict[int, ProcInfo] = {}
        names: dict[int, tuple[bytes, str]] = {}
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            stat = _read(f"/proc/{entry}/stat")
            if not stat:
                continue
            pid = int(entry)
            end = stat.rindex(b")")
            f = stat[end + 2:].split()      # f[0] is field 3 (state) of proc(5)
            start = f[19]
            # A name is looked up once per process: the key changes when the pid is
            # reused (start time) or the process renames itself (comm).
            key = start + stat[:end]
            cached = self._names.get(pid)
            if cached is not None and cached[0] == key:
                name = cached[1]
            else:
                name = stat[stat.index(b"(") + 1:end].decode(errors="replace")
                if len(name) >= _COMM_MAX:
                    name = _full_name(entry, name)
            names[pid] = (key, name)
            read_bytes = write_bytes = 0
            for line in _read(f"/proc/{entry}/io").splitlines():
                if line.startswith(b"read_bytes:"):
                    read_bytes = int(line[11:])
                elif line.startswith(b"write_bytes:"):
                    write_bytes = int(line[12:])
            rss = int(f[21]) * _PAGE
            out[pid] = ProcInfo(
                pid=pid, name=name,
                cpu_time=(int(f[11]) + int(f[12])) / _HZ,
                private_ws=rss, working_set=rss,
                read_bytes=read_bytes, write_bytes=write_bytes,
                threads=int(f[17]), handles=0,
                create_time=self._boot + int(start) / _HZ,
            )
        self._names = names
        return out


def create_snapshot_source():
    """Return a callable producing {pid: ProcInfo}, or None if unsupported here."""
    try:
        src = LinuxProcessSnapshot()
        if os.getpid() not in src():      # smoke test
            raise OSError("own process missing from /proc")
        return src
    except Exception as e:
        log.info("/proc process snapshot unavailable: %s", e)
        return None
