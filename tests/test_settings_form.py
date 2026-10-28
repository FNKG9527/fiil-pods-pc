"""设置表单描述的离线测试：覆盖完整性、往返转换、非法输入。"""

from __future__ import annotations

import pytest

import settings as st
import settings_form as sf


def test_every_model_field_has_a_form_row():
    # 给 Settings 加字段却忘了加界面时，这条会立刻失败
    assert set(st.field_names()) == {row.key for row in sf.all_rows()}


def test_form_rows_match_field_rules():
    rules = st.field_rules()
    for row in sf.all_rows():
        caster, _ = rules[row.key]
        expected = sf.BOOL if caster is bool else sf.NUMBER
        assert row.kind == expected, row.key


def test_values_round_trip():
    values = sf.to_values(st.DEFAULTS)
    restored, warnings = sf.from_values(st.DEFAULTS, values)
    assert restored == st.DEFAULTS
    assert warnings == []


def test_float_rows_keep_one_decimal():
    values = sf.to_values(st.DEFAULTS.with_changes(scan_timeout=8.0))
    assert values["scan_timeout"] == "8.0"
    restored, _ = sf.from_values(st.DEFAULTS, values)
    assert restored.scan_timeout == 8.0


def test_bad_number_keeps_old_value_and_warns():
    values = sf.to_values(st.DEFAULTS)
    values["log_font_size"] = "十二"
    restored, warnings = sf.from_values(st.DEFAULTS, values)
    assert restored.log_font_size == st.DEFAULTS.log_font_size
    assert len(warnings) == 1 and "日志字号" in warnings[0]


def test_out_of_range_is_clamped_with_message():
    values = sf.to_values(st.DEFAULTS)
    values["log_max_lines"] = "999999"
    restored, warnings = sf.from_values(st.DEFAULTS, values)
    assert restored.log_max_lines == 100_000
    assert "超出" in warnings[0]


def test_empty_number_is_rejected():
    values = sf.to_values(st.DEFAULTS)
    values["connect_timeout"] = "   "
    restored, warnings = sf.from_values(st.DEFAULTS, values)
    assert restored.connect_timeout == st.DEFAULTS.connect_timeout
    assert "不能为空" in warnings[0]


def test_booleans_accept_checkbox_values():
    values = sf.to_values(st.DEFAULTS)
    values["auto_hide_to_tray"] = 1
    values["confirm_before_exit"] = 0
    restored, _ = sf.from_values(st.DEFAULTS, values)
    assert restored.auto_hide_to_tray is True
    assert restored.confirm_before_exit is False


def test_describe_lists_every_section_and_field():
    text = sf.describe(st.DEFAULTS)
    for section in sf.SECTIONS:
        assert f"[{section.title}]" in text
    for row in sf.all_rows():
        assert row.label in text


def test_missing_key_in_values_is_ignored():
    # 只提交部分字段（例如某个标签页没打开）时，其余字段保持不变
    partial = {"log_font_size": "12"}
    restored, warnings = sf.from_values(st.DEFAULTS, partial)
    assert restored.log_font_size == 12
    assert restored.scan_timeout == st.DEFAULTS.scan_timeout
    assert warnings == []


def test_from_widget_rejects_float_for_int_row():
    row = next(r for r in sf.all_rows() if r.key == "log_font_size")
    with pytest.raises(ValueError):
        row.from_widget("9.5x")
