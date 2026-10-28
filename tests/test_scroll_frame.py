# -*- coding: utf-8 -*-
"""滚动容器的纯逻辑测试（不创建 Tk 窗口，可在无显示环境跑）。

几何数字来自真实测量：主窗口 1280x820 时左栏只有 682px 高，
而 7 个面板的自然高度合计约 1033px —— 底部两个面板因此被压扁/消失。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scroll_frame


def test_wheel_up_moves_toward_top():
    """Windows 上滚轮向上 delta=+120，对应 yview_scroll 的负数。"""
    assert scroll_frame.wheel_steps(120) == -3


def test_wheel_down_moves_toward_bottom():
    assert scroll_frame.wheel_steps(-120) == 3


def test_wheel_multiple_notches_scale_linearly():
    assert scroll_frame.wheel_steps(240) == -6
    assert scroll_frame.wheel_steps(-360) == 9


def test_wheel_zero_is_noop():
    """部分鼠标/触控板会发 delta=0，不能当成一次滚动。"""
    assert scroll_frame.wheel_steps(0) == 0


def test_scrollbar_needed_when_content_taller_than_view():
    assert scroll_frame.needs_scrollbar(1033, 682) is True


def test_scrollbar_hidden_when_content_fits():
    assert scroll_frame.needs_scrollbar(600, 682) is False


def test_scrollbar_tolerates_rounding_error_of_one_pixel():
    """Canvas 的 scrollregion 与可视高度常有 1px 抖动，不能因此闪出滚动条。"""
    assert scroll_frame.needs_scrollbar(682, 682) is False
    assert scroll_frame.needs_scrollbar(683, 682) is False
    assert scroll_frame.needs_scrollbar(684, 682) is True
