"""主题模块的离线测试。"""

from __future__ import annotations

import pytest

import ui_theme as t


def test_every_palette_value_is_a_hex_color():
    for name, value in t.PALETTE.items():
        assert value.startswith("#") and len(value) == 7, name
        int(value[1:], 16)


def test_color_returns_declared_value_and_rejects_unknown():
    assert t.color("ok") == t.PALETTE["ok"]
    with pytest.raises(KeyError):
        t.color("没有这个颜色")


def test_tone_color_maps_off_to_muted_and_unknown_to_text():
    assert t.tone_color("off") == t.PALETTE["muted"]
    assert t.tone_color("ok") == t.PALETTE["ok"]
    assert t.tone_color("warn") == t.PALETTE["warn"]
    assert t.tone_color("什么鬼") == t.PALETTE["text"]


def test_log_tags_all_resolve():
    for name in t.LOG_TAG_COLORS.values():
        assert t.color(name)


def test_palette_has_no_duplicate_accident():
    # 允许重复（例如两处同色），但至少主语义色必须互不相同，避免「已连接」和「错误」同色
    must_differ = ["ok", "warn", "error", "accent"]
    values = [t.PALETTE[k] for k in must_differ]
    assert len(set(values)) == len(values)
