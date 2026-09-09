from mimir.metrics import (CHART_GROUPS, PROCESS_METRIC_BY_ID, PROCESS_METRICS, SYSTEM_METRIC_BY_ID,
                           axis_scale, format_bytes, format_rate, format_value)
from mimir.settings import Settings


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1023) == "1023 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(1536 * 1024) == "1.5 MB"
    assert format_bytes(3 * 1024 ** 3) == "3.0 GB"
    assert format_rate(2048) == "2.0 KB/s"


def test_format_value_by_kind():
    assert format_value(PROCESS_METRIC_BY_ID["cpu"], 12.345) == "12.3%"
    assert format_value(PROCESS_METRIC_BY_ID["mem"], 2 * 1024 ** 2) == "2.0 MB"
    assert format_value(PROCESS_METRIC_BY_ID["threads"], 7) == "7"
    assert axis_scale(PROCESS_METRIC_BY_ID["mem"]) * 1024 ** 2 == 1.0


def test_chart_groups_reference_real_metrics():
    for g in CHART_GROUPS:
        for m in g.metrics:
            assert m in PROCESS_METRIC_BY_ID
        for m in g.system_metrics:
            assert m in SYSTEM_METRIC_BY_ID
    assert len({m.id for m in PROCESS_METRICS}) == len(PROCESS_METRICS)


def test_settings_validate_and_roundtrip(tmp_path):
    s = Settings(sample_interval=0.01, table_rows=99999, sort_field="bogus")
    s.validate()
    assert s.sample_interval == 0.2 and s.table_rows == 500 and s.sort_field == "current"
    p = tmp_path / "settings.json"
    s.chart_seconds = 42
    s.save(p)
    loaded = Settings.load(p)
    assert loaded.chart_seconds == 42
    assert loaded.max_snapshots == int(loaded.history_seconds / loaded.sample_interval) + 2
    # unknown keys and junk files are tolerated
    p.write_text('{"nope": 1, "chart_seconds": 30}')
    assert Settings.load(p).chart_seconds == 30
    p.write_text("not json")
    assert Settings.load(p).chart_seconds == Settings().chart_seconds
