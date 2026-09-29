import shutil
import subprocess
import sys
import time

import pytest

from mimir.linproc import create_snapshot_source

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc")
psutil = pytest.importorskip("psutil")


def test_snapshot_matches_psutil_for_every_process():
    src = create_snapshot_source()
    assert src is not None
    snap = src()
    checked = 0
    for p in psutil.process_iter():
        info = snap.get(p.pid)
        if info is None:            # started after the snapshot
            continue
        try:
            with p.oneshot():
                name, threads, rss = p.name(), p.num_threads(), p.memory_info().rss
                kernel_thread = p.pid == 2 or p.ppid() == 2
                cpu = p.cpu_times()
                create = p.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not kernel_thread:       # kworkers rename themselves many times a second
            assert info.name == name, p.pid
        assert abs(info.create_time - create) < 1.0, p.pid
        # the two reads are a moment apart, so a busy process can move a little in between
        assert abs(info.threads - threads) <= 4, p.pid
        assert abs(info.private_ws - rss) <= max(8 << 20, rss // 10), p.pid
        assert -1e-6 <= (cpu.user + cpu.system) - info.cpu_time < 1.0, p.pid
        checked += 1
    assert checked > 10


def test_io_counters_match_psutil_for_own_process():
    me = psutil.Process()
    info = create_snapshot_source()()[me.pid]
    io = me.io_counters()
    assert abs(info.read_bytes - io.read_bytes) < 1_000_000
    assert abs(info.write_bytes - io.write_bytes) < 1_000_000


def test_long_name_comes_from_cmdline(tmp_path):
    exe = tmp_path / "mimir_long_name_test"
    shutil.copy("/bin/sleep", exe)
    proc = subprocess.Popen([str(exe), "30"])
    try:
        time.sleep(0.2)
        info = create_snapshot_source()()[proc.pid]
        assert info.name == "mimir_long_name_test" == psutil.Process(proc.pid).name()
    finally:
        proc.kill()
        proc.wait()
