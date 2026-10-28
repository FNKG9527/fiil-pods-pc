"""设置模型的离线测试：默认值、容错、范围钳制、原子读写。"""

from __future__ import annotations

import json

import settings as st


def test_defaults_are_sane_and_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    st.save(path, st.DEFAULTS)
    loaded, warnings = st.load(path)
    assert loaded == st.DEFAULTS
    assert warnings == []
    # 文件必须是 UTF-8 可读的 JSON 对象，且键与模型一致
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == set(st.field_names())


def test_missing_file_returns_defaults_without_warning(tmp_path):
    loaded, warnings = st.load(tmp_path / "nope.json")
    assert loaded == st.DEFAULTS
    assert warnings == []


def test_broken_json_falls_back_to_defaults_and_explains(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    loaded, warnings = st.load(path)
    assert loaded == st.DEFAULTS
    assert len(warnings) == 1 and "损坏" in warnings[0]


def test_unknown_keys_are_dropped_and_reported(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"log_font_size": 11, "from_the_future": 1}), encoding="utf-8")
    loaded, warnings = st.load(path)
    assert loaded.log_font_size == 11
    assert any("from_the_future" in w for w in warnings)


def test_wrong_types_keep_defaults_and_are_reported(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "log_font_size": "很大",
        "auto_connect": "不是布尔",
        "scan_timeout": [1, 2],
    }), encoding="utf-8")
    loaded, warnings = st.load(path)
    assert loaded == st.DEFAULTS
    assert len(warnings) == 3


def test_out_of_range_numbers_are_clamped_not_rejected():
    loaded, warnings = st.coerce({"log_font_size": 99, "scan_timeout": 0.1})
    assert loaded.log_font_size == 20
    assert loaded.scan_timeout == 2.0
    assert len(warnings) == 2 and all("超出范围" in w for w in warnings)


def test_string_booleans_are_accepted_for_hand_edited_files():
    loaded, warnings = st.coerce({"autoscroll": "false", "auto_connect": "是"})
    assert loaded.autoscroll is False
    assert loaded.auto_connect is True
    assert warnings == []


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    st.save(path, st.DEFAULTS.with_changes(log_font_size=12))
    assert path.exists()
    assert not (path.with_name("settings.json.tmp")).exists()
    loaded, _ = st.load(path)
    assert loaded.log_font_size == 12


def test_bool_is_not_accepted_as_number():
    # True 是 int 的子类，若不显式拦截会被当成 1 写进数值字段
    loaded, warnings = st.coerce({"log_font_size": True})
    assert loaded.log_font_size == st.DEFAULTS.log_font_size
    assert len(warnings) == 1
