import subprocess
import sys
import time

import psutil

from mimir.killer import kill_processes


def _spawn_tree():
    """A parent that spawns a child; both sleep for a minute."""
    code = ("import subprocess, sys, time; "
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "time.sleep(60)")
    parent = subprocess.Popen([sys.executable, "-c", code])
    deadline = time.time() + 10
    while time.time() < deadline:
        kids = psutil.Process(parent.pid).children()
        if kids:
            return parent, kids[0].pid
        time.sleep(0.1)
    parent.kill()
    raise RuntimeError("child did not start")


def test_kill_tree_terminates_parent_and_child():
    parent, child_pid = _spawn_tree()
    report = kill_processes([parent.pid], include_children=True)
    assert parent.pid in report.killed and child_pid in report.killed, report.summary
    assert not report.failed
    time.sleep(0.2)
    assert not psutil.pid_exists(child_pid)
    assert parent.poll() is not None


def test_kill_without_children_leaves_child():
    parent, child_pid = _spawn_tree()
    try:
        report = kill_processes([parent.pid], include_children=False)
        assert report.killed == [parent.pid], report.summary
        assert psutil.pid_exists(child_pid)
    finally:
        try:
            psutil.Process(child_pid).kill()
        except psutil.NoSuchProcess:
            pass


def test_never_kills_self_and_tolerates_missing_pids():
    import os
    report = kill_processes([os.getpid(), 999_999_999], include_children=False)
    assert report.skipped_self and not report.killed
