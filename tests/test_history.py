import time

from mimir.history import HistoryStore, Snapshot
from mimir.metrics import PROCESS_METRIC_INDEX, PROCESS_METRICS

N = len(PROCESS_METRICS)
I_CPU = PROCESS_METRIC_INDEX["cpu"]
I_MEM = PROCESS_METRIC_INDEX["mem"]


def proc(pid, name, cpu=0.0, mem=0.0):
    v = [0.0] * N
    v[I_CPU] = cpu
    v[I_MEM] = mem
    return pid, name, v


def snap(ts, procs, system=None):
    return Snapshot.pack(ts, procs, system or {})


def test_peaks_track_value_and_time_per_pid_and_name():
    store = HistoryStore(100)
    t0 = time.time() - 10
    store.append(snap(t0, [proc(1, "a.exe", cpu=5), proc(2, "a.exe", cpu=1)]))
    store.append(snap(t0 + 1, [proc(1, "a.exe", cpu=2), proc(2, "a.exe", cpu=9)]))
    store.append(snap(t0 + 2, [proc(1, "a.exe", cpu=3)]))

    p1 = store.peaks_for(("pid", 1))[I_CPU]
    assert p1.value == 5 and p1.ts == t0
    p2 = store.peaks_for(("pid", 2))[I_CPU]
    assert p2.value == 9 and p2.ts == t0 + 1
    # group peak is the peak of the per-tick sum, not the sum of peaks
    pg = store.peaks_for(("name", "a.exe"))[I_CPU]
    assert pg.value == 11 and pg.ts == t0 + 1


def test_rankings_group_and_sort():
    store = HistoryStore(100)
    now = time.time()
    store.append(snap(now - 2, [proc(1, "a.exe", cpu=10), proc(2, "b.exe", cpu=1), proc(3, "a.exe", cpu=4)]))
    store.append(snap(now - 1, [proc(1, "a.exe", cpu=2), proc(2, "b.exe", cpu=6), proc(3, "a.exe", cpu=4)]))

    rows = store.rankings(seconds=60, grouped=True, sort_metric="cpu", sort_field="current")
    assert [r.name for r in rows] == ["a.exe", "b.exe"]     # current: a = 2+4 = 6, b = 6 -> insertion order on tie
    a = rows[0]
    assert a.pids == [1, 3]
    assert a.current[I_CPU] == 6
    assert a.average[I_CPU] == (14 + 6) / 2
    assert a.maximum[I_CPU] == 14
    assert a.label == "a.exe (2)"

    rows = store.rankings(seconds=60, grouped=False, sort_metric="cpu", sort_field="average")
    assert [r.pids[0] for r in rows] == [1, 3, 2]   # avg 6, 4, 3.5
    rows = store.rankings(seconds=60, grouped=False, sort_metric="cpu", sort_field="maximum", descending=False)
    assert [r.pids[0] for r in rows] == [3, 2, 1]   # max 4, 6, 10


def test_rankings_filters_and_dead_processes():
    store = HistoryStore(100)
    now = time.time()
    store.append(snap(now - 2, [proc(1, "gone.exe", cpu=50), proc(2, "System Idle Process", cpu=90)]))
    store.append(snap(now - 1, [proc(3, "keep.exe", cpu=1), proc(2, "System Idle Process", cpu=90)]))

    rows = store.rankings(seconds=60, grouped=False, hide_idle=True)
    names = {r.name: r for r in rows}
    assert "System Idle Process" not in names
    assert names["gone.exe"].alive is False and names["gone.exe"].current[I_CPU] == 0
    assert names["keep.exe"].alive is True

    rows = store.rankings(seconds=60, grouped=False, hide_idle=False, name_filter="idle")
    assert [r.name for r in rows] == ["System Idle Process"]

    rows = store.rankings(seconds=60, grouped=False, only_alive=True)
    assert {r.name for r in rows} == {"keep.exe"}


def test_series_and_window():
    store = HistoryStore(100)
    now = time.time()
    for i in range(5):
        store.append(snap(now - 4 + i, [proc(1, "a.exe", cpu=i, mem=i * 10)], {"cpu": i * 2.0}))

    b = store.entity_series(("pid", 1), ["cpu", "mem"], seconds=2.5)
    assert len(b.ts) == 3
    assert b.values["cpu"] == [2, 3, 4]
    assert b.values["mem"] == [20, 30, 40]

    s = store.system_series(["cpu"], seconds=100)
    assert s.values["cpu"] == [0, 2, 4, 6, 8]
    assert store.system_peak("cpu").value == 8
    assert store.span_seconds() == 4


def test_maxlen_and_clear():
    store = HistoryStore(3)
    now = time.time()
    for i in range(6):
        store.append(snap(now + i, [proc(1, "a.exe", cpu=i)]))
    assert len(store) == 3
    store.set_maxlen(10)
    assert len(store) == 3 and store.maxlen == 10
    store.clear()
    assert len(store) == 0
    assert store.peaks_for(("pid", 1)) == [None] * N


def test_current_cpu_is_averaged_over_smoothing_window():
    store = HistoryStore(100)
    now = time.time()
    # a.exe spikes on the last sample; b.exe is steady. 5 samples 1 s apart.
    for i, a_cpu in enumerate([0, 0, 0, 0, 50]):
        store.append(snap(now - 4 + i, [proc(1, "a.exe", cpu=a_cpu), proc(2, "b.exe", cpu=20)]))

    raw = {r.name: r.current[I_CPU] for r in store.rankings(seconds=30, grouped=True)}
    assert raw == {"a.exe": 50, "b.exe": 20}

    rows = store.rankings(seconds=30, grouped=True, cpu_smooth=5)
    assert [r.name for r in rows] == ["b.exe", "a.exe"]     # the spike no longer jumps to the top
    assert rows[0].current[I_CPU] == 20 and rows[1].current[I_CPU] == 10
