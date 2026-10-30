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


def test_value_font_is_not_oversized():
    # v1.4.0：界面「太大」的主要来源是 18/20 号的大数值，压到 13 号
    assert t.FONT_SIZE_VALUE <= 14, f"数值字号 {t.FONT_SIZE_VALUE} 仍然过大"
    assert t.FONT_SIZE_VALUE > t.FONT_SIZE_BASE, "数值仍要比正文醒目"
    family, size, weight = t.value_font()
    assert family == t.FONT_UI and size == t.FONT_SIZE_VALUE and weight == "bold"


def test_font_ladder_is_monotonic():
    assert t.FONT_SIZE_SMALL < t.FONT_SIZE_BASE < t.FONT_SIZE_TITLE <= t.FONT_SIZE_BRAND
    assert t.FONT_SIZE_BRAND < t.FONT_SIZE_VALUE


def test_tone_soft_covers_every_tone_used_by_overview():
    import overview as ov

    for tone in ov.TONES:
        soft = t.tone_soft(tone)
        assert soft in t.PALETTE.values(), f"{tone} 没有淡底色"


def test_tone_soft_unknown_tone_falls_back():
    assert t.tone_soft("什么鬼") == t.PALETTE["card_alt"]


def test_palette_has_surface_hierarchy():
    # 「素」的根因是只有一种底色：窗口底、卡片、次级面现在必须区分开
    for name in ("bg", "card", "card_alt", "border", "border_soft",
                 "accent_soft", "ok_soft", "warn_soft", "error_soft",
                 "header_bg", "header_text", "faint"):
        assert name in t.PALETTE, name


def test_spacing_scale_is_ascending():
    vals = [t.SPACE[k] for k in ("xs", "sm", "md", "lg", "xl")]
    assert vals == sorted(vals) and len(set(vals)) == len(vals)
    assert t.SPACE["xs"] >= 0


def test_ui_font_helpers():
    assert t.ui_font() == (t.FONT_UI, t.FONT_SIZE_BASE)
    assert t.ui_font(12) == (t.FONT_UI, 12)
    assert t.mono_font() == (t.FONT_MONO, t.FONT_SIZE_BASE)


def test_left_pane_narrowed_from_legacy_470():
    # 原来左栏 470px 太宽，右侧总览被挤；收窄到 430 以内
    assert t.LEFT_PANE_WIDTH <= 440
    assert t.LEFT_PANE_WIDTH >= 400, "太窄会放不下五档 MAF 按钮"
