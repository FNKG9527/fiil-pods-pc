"""状态总览模型的离线测试。"""

from __future__ import annotations

from types import SimpleNamespace

import overview as ov


def test_disconnected_shows_all_unknown():
    items = ov.build_overview(connected=False, maf=1, cinema=True, tone=0)
    assert all(i.value == "--" and i.tone == "idle" for i in items)
    assert [i.key for i in items] == [
        "battery", "maf", "low_latency", "cinema", "multipoint", "tone", "ldac",
    ]


def test_connected_values_are_mapped_to_fiil_labels():
    items = {i.key: i for i in ov.build_overview(
        connected=True, maf=1, low_latency=0, cinema=False, multipoint=True, tone=3)}
    assert items["maf"].value == "降噪" and items["maf"].tone == "ok"
    assert items["low_latency"].value == "关闭" and items["low_latency"].tone == "off"
    assert items["cinema"].value == "关" and items["cinema"].tone == "off"
    assert items["multipoint"].value == "开"
    assert items["tone"].value == "静音"


def test_unknown_fields_stay_unknown_even_when_connected():
    items = {i.key: i for i in ov.build_overview(connected=True)}
    assert items["maf"].value == "--" and items["maf"].tone == "idle"
    assert items["cinema"].value == "--"


def test_single_battery_model_collapses_to_one_card():
    battery = SimpleNamespace(single=88, left=None, right=None, case=None)
    items = ov.battery_items(battery)
    assert len(items) == 1
    assert items[0].label == "耳机电量" and items[0].value == "88%"


def test_three_battery_model_shows_three_cards():
    battery = SimpleNamespace(single=None, left=90, right=None, case=77)
    items = ov.battery_items(battery)
    assert [i.value for i in items] == ["90%", "--", "77%"]
    assert [i.tone for i in items] == ["ok", "idle", "ok"]


def test_no_battery_object_gives_placeholder():
    items = ov.battery_items(None)
    assert items[0].value == "--" and items[0].tone == "idle"


def test_connection_text_tones():
    assert ov.connection_text(True) == ("已连接", "ok")
    assert ov.connection_text(False) == ("未连接", "idle")
    assert ov.connection_text(False, switching=True)[1] == "warn"
