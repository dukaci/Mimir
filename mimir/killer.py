"""Forceful process termination.

Strength ladder, all attempted in order until the target is gone:
1. SeDebugPrivilege is enabled on our token (works when elevated) so that
   processes owned by other users or services can be opened for termination.
2. Descendants are terminated first (deepest first) so nothing respawns or
   lingers as an orphan, then the target itself, via TerminateProcess
   (psutil.Process.kill), which the process cannot catch or refuse.
3. Anything still alive is handed to `taskkill /F /T` as a last resort.

Protected processes (System, csrss, PPL services, anti-cheat drivers) cannot be
killed from user mode even as administrator; they are reported as failures.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
from dataclasses import dataclass, field

import psutil

from .platform import IS_WINDOWS

log = logging.getLogger(__name__)

_debug_privilege_state: bool | None = None


def enable_debug_privilege() -> bool:
    """Turn on SeDebugPrivilege for this process (Windows, needs an elevated token)."""
    global _debug_privilege_state
    if _debug_privilege_state is not None:
        return _debug_privilege_state
    if not IS_WINDOWS:
        _debug_privilege_state = False
        return False
    try:
        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32
        TOKEN_ADJUST_PRIVILEGES, TOKEN_QUERY, SE_PRIVILEGE_ENABLED = 0x20, 0x8, 0x2

        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]

        class LUID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Luid", LUID), ("Attributes", ctypes.c_ulong)]

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [("PrivilegeCount", ctypes.c_ulong), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

        token = ctypes.c_void_p()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                         ctypes.byref(token)):
            raise OSError("OpenProcessToken failed")
        try:
            luid = LUID()
            if not advapi32.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
                raise OSError("LookupPrivilegeValue failed")
            tp = TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            tp.Privileges[0].Luid = luid
            tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
            advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
            ok = kernel32.GetLastError() == 0        # ERROR_NOT_ALL_ASSIGNED when not elevated
        finally:
            kernel32.CloseHandle(token)
    except Exception as e:
        log.debug("SeDebugPrivilege not enabled: %s", e)
        ok = False
    _debug_privilege_state = ok
    return ok


@dataclass
class KillReport:
    requested: list[int] = field(default_factory=list)
    killed: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)
    skipped_self: bool = False

    @property
    def summary(self) -> str:
        parts = [f"ended {len(self.killed)} process{'es' if len(self.killed) != 1 else ''}"]
        if self.failed:
            names = ", ".join(f"{pid} ({why})" for pid, why in list(self.failed.items())[:3])
            more = f" +{len(self.failed) - 3} more" if len(self.failed) > 3 else ""
            parts.append(f"could not end {len(self.failed)}: {names}{more}")
        if self.skipped_self:
            parts.append("skipped Mimir itself")
        return "; ".join(parts)


def _collect_targets(pids: list[int], include_children: bool) -> list[psutil.Process]:
    seen: dict[int, psutil.Process] = {}
    for pid in pids:
        try:
            p = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if include_children:
            try:
                for c in p.children(recursive=True):
                    seen.setdefault(c.pid, c)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        seen.setdefault(p.pid, p)
    # deepest descendants first so parents cannot restart them
    def depth(proc: psutil.Process) -> int:
        d = 0
        try:
            cur = proc
            while cur.pid in seen and cur.ppid() in seen and cur.ppid() != cur.pid:
                cur = seen[cur.ppid()]
                d += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return d
    return sorted(seen.values(), key=depth, reverse=True)


def kill_processes(pids: list[int], include_children: bool = True, timeout: float = 2.0) -> KillReport:
    report = KillReport(requested=list(pids))
    me = os.getpid()
    enable_debug_privilege()
    targets = _collect_targets(pids, include_children)
    if any(p.pid == me for p in targets):
        report.skipped_self = True
        targets = [p for p in targets if p.pid != me]
    if not targets:
        return report

    for p in targets:
        try:
            p.kill()                       # TerminateProcess on Windows, SIGKILL elsewhere
        except psutil.NoSuchProcess:
            report.killed.append(p.pid)
        except psutil.AccessDenied as e:
            report.failed[p.pid] = "access denied"
        except Exception as e:
            report.failed[p.pid] = str(e)

    gone, alive = psutil.wait_procs([p for p in targets if p.pid not in report.failed], timeout=timeout)
    report.killed.extend(p.pid for p in gone)

    # last resort for anything still alive or denied
    stubborn = [p.pid for p in alive] + list(report.failed)
    if stubborn and IS_WINDOWS:
        for pid in stubborn:
            try:
                r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, text=True,
                                   timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if not psutil.pid_exists(pid):
                    report.failed.pop(pid, None)
                    report.killed.append(pid)
                else:
                    msg = (r.stderr or r.stdout).strip().splitlines()
                    report.failed[pid] = msg[-1][:60] if msg else "still running"
            except Exception as e:
                report.failed[pid] = str(e)[:60]
    elif stubborn:
        for pid in stubborn:
            if psutil.pid_exists(pid):
                report.failed.setdefault(pid, "still running")
            else:
                report.failed.pop(pid, None)
                report.killed.append(pid)

    report.killed = sorted(set(report.killed))
    log.info("kill %s -> %s", pids, report.summary)
    return report
