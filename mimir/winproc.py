"""Fast whole-system process snapshot on Windows.

One NtQuerySystemInformation(SystemProcessInformation) call returns every
process with its CPU times, memory, I/O counters, thread and handle counts.
This is what Task Manager uses; it costs a few milliseconds for hundreds of
processes and needs no per-process handles, so protected processes are
included too.  psutil does the same job with one open-process round trip per
process, which is two orders of magnitude slower.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as W
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

_SPI_CLASS = 5                                  # SystemProcessInformation
_STATUS_INFO_LENGTH_MISMATCH = -1073741820      # 0xC0000004


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", W.USHORT), ("MaximumLength", W.USHORT), ("Buffer", ctypes.c_void_p)]


class _SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", W.ULONG),
        ("NumberOfThreads", W.ULONG),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", W.ULONG),
        ("NumberOfThreadsHighWatermark", W.ULONG),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UNICODE_STRING),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", W.ULONG),
        ("SessionId", W.ULONG),
        ("UniqueProcessKey", ctypes.c_void_p),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", W.ULONG),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivatePageCount", ctypes.c_size_t),
        ("ReadOperationCount", ctypes.c_longlong),
        ("WriteOperationCount", ctypes.c_longlong),
        ("OtherOperationCount", ctypes.c_longlong),
        ("ReadTransferCount", ctypes.c_longlong),
        ("WriteTransferCount", ctypes.c_longlong),
        ("OtherTransferCount", ctypes.c_longlong),
    ]


@dataclass(slots=True)
class ProcInfo:
    pid: int
    name: str
    cpu_time: float          # seconds of user + kernel time, cumulative
    private_ws: int          # private working set, bytes (Task Manager's "Memory")
    working_set: int
    read_bytes: int          # cumulative
    write_bytes: int         # cumulative
    threads: int
    handles: int
    create_time: float       # epoch seconds


class WindowsProcessSnapshot:
    """Callable that returns {pid: ProcInfo} for every process."""

    def __init__(self):
        ntdll = ctypes.WinDLL("ntdll")
        self._query = ntdll.NtQuerySystemInformation
        self._query.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        self._query.restype = ctypes.c_long
        self._buf = ctypes.create_string_buffer(1 << 20)

    def __call__(self) -> dict[int, ProcInfo]:
        need = ctypes.c_ulong(0)
        status = self._query(_SPI_CLASS, self._buf, len(self._buf), ctypes.byref(need))
        while status == _STATUS_INFO_LENGTH_MISMATCH:
            self._buf = ctypes.create_string_buffer(need.value + (1 << 16))
            status = self._query(_SPI_CLASS, self._buf, len(self._buf), ctypes.byref(need))
        if status != 0:
            raise OSError(f"NtQuerySystemInformation failed: 0x{status & 0xFFFFFFFF:08X}")

        out: dict[int, ProcInfo] = {}
        offset = 0
        struct_size = ctypes.sizeof(_SYSTEM_PROCESS_INFORMATION)
        buf = self._buf
        while True:
            p = _SYSTEM_PROCESS_INFORMATION.from_buffer(buf, offset)
            pid = p.UniqueProcessId or 0
            if p.ImageName.Buffer and p.ImageName.Length:
                name = ctypes.wstring_at(p.ImageName.Buffer, p.ImageName.Length // 2)
            else:
                name = "System Idle Process" if pid == 0 else f"pid {pid}"
            out[pid] = ProcInfo(
                pid=pid, name=name,
                cpu_time=(p.UserTime + p.KernelTime) / 1e7,
                private_ws=p.WorkingSetPrivateSize, working_set=p.WorkingSetSize,
                read_bytes=p.ReadTransferCount, write_bytes=p.WriteTransferCount,
                threads=p.NumberOfThreads, handles=p.HandleCount,
                create_time=(p.CreateTime / 1e7) - 11644473600.0 if p.CreateTime else 0.0,
            )
            if not p.NextEntryOffset:
                break
            offset += p.NextEntryOffset
            if offset + struct_size > len(buf):
                break
        return out


def create_snapshot_source():
    """Return a callable producing {pid: ProcInfo}, or None if unsupported here."""
    try:
        src = WindowsProcessSnapshot()
        src()      # smoke test
        return src
    except Exception as e:      # not Windows, or the struct layout does not match
        log.info("kernel process snapshot unavailable: %s", e)
        return None
