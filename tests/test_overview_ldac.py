"""状态总览新增 LDAC 卡片的离线测试。"""

from __future__ import annotations

import overview as ov


def test_overview_has_ldac_card_after_tone():
    items = ov.build_overview(connected=True)
    assert [i.key for i in items] == [
        "battery", "maf", "low_latency", "cinema", "multipoint", "tone", "ldac",
    ]


def test_ldac_shows_on_off_when_known():
    on = {i.key: i for i in ov.build_overview(connected=True, ldac=True, ldac_unlocked=True)}
    assert on["ldac"].value == "开" and on["ldac"].tone == "ok"
    off = {i.key: i for i in ov.build_overview(connected=True, ldac=False, ldac_unlocked=True)}
    assert off["ldac"].value == "关" and off["ldac"].tone == "off"


def test_ldac_shows_locked_hint_when_not_activated():
    """未激活时不能只显示「关」——那会让人以为是开关坏了，要写明需先在 App 激活。"""
    items = {i.key: i for i in ov.build_overview(
        connected=True, ldac=False, ldac_unlocked=False)}
    assert items["ldac"].value == "未激活"
    assert items["ldac"].tone == "warn"


def test_ldac_unknown_when_not_read():
    items = {i.key: i for i in ov.build_overview(connected=True)}
    assert items["ldac"].value == "--" and items["ldac"].tone == "idle"
