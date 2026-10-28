"""日志缓冲的离线测试：容量上限、CSV 只收帧、缩容与清空。"""

from __future__ import annotations

from log_store import LogStore


def test_entries_keep_order_and_line_format():
    store = LogStore()
    store.add("sys", "10:00:00", "就绪")
    store.add("tx", "10:00:01", "TX   GET_BATTERY", direction="TX")
    lines = store.as_text().splitlines()
    assert lines == ["10:00:00  就绪", "10:00:01  TX   GET_BATTERY"]


def test_capacity_drops_oldest_and_counts_dropped():
    store = LogStore(capacity=3)
    for i in range(5):
        store.add("sys", f"10:00:0{i}", f"第 {i} 条")
    assert len(store) == 3
    assert store.dropped == 2
    assert [e.text for e in store.entries()] == ["第 2 条", "第 3 条", "第 4 条"]


def test_csv_rows_only_include_frames():
    store = LogStore()
    store.add("sys", "10:00:00", "就绪")
    store.add("tx", "10:00:01", "TX   00 27 01", direction="TX")
    store.add("rx", "10:00:02", "RX   00 27 02", direction="RX")
    store.add("parse", "10:00:03", "电量 88%")
    rows = store.csv_rows()
    assert [r["dir"] for r in rows] == ["TX", "RX"]
    assert rows[0]["data"] == "TX   00 27 01"


def test_set_capacity_shrinks_and_reports_loss():
    store = LogStore(capacity=10)
    for i in range(6):
        store.add("sys", "", f"第 {i} 条")
    assert store.set_capacity(2) == 4
    assert [e.text for e in store.entries()] == ["第 4 条", "第 5 条"]
    # 扩容不丢东西
    assert store.set_capacity(100) == 0
    assert len(store) == 2


def test_clear_resets_everything():
    store = LogStore(capacity=2)
    for i in range(4):
        store.add("sys", "", str(i))
    store.clear()
    assert len(store) == 0 and store.dropped == 0 and store.as_text() == ""


def test_capacity_is_at_least_one():
    store = LogStore(capacity=0)
    store.add("sys", "", "a")
    store.add("sys", "", "b")
    assert len(store) == 1
    assert store.entries()[0].text == "b"
