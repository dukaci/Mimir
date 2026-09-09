import os
import sys
import threading

import pytest

from mimir.winproc import create_snapshot_source

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows kernel API")


def test_snapshot_matches_psutil_for_own_process():
    psutil = pytest.importorskip("psutil")
    src = create_snapshot_source()
    assert src is not None
    snap = src()
    me = snap[os.getpid()]
    p = psutil.Process()
    assert me.name.lower() == p.name().lower()
    assert me.threads == p.num_threads() or abs(me.threads - threading.active_count()) < 8
    io = p.io_counters()
    assert abs(me.read_bytes - io.read_bytes) < 1_000_000
    assert abs(me.write_bytes - io.write_bytes) < 1_000_000
    mem = p.memory_info()
    assert 0 < me.private_ws <= mem.rss * 1.5 + 1_000_000
    assert me.cpu_time > 0
    assert abs(me.create_time - p.create_time()) < 2


def test_snapshot_covers_system_processes():
    src = create_snapshot_source()
    snap = src()
    assert 0 in snap and snap[0].name == "System Idle Process"
    assert 4 in snap and snap[4].name == "System"
    assert len(snap) > 20
