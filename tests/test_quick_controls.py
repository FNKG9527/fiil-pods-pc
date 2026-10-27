"""快捷控制目录与托盘菜单模型的离线测试（不启动 GUI / 不连蓝牙）。"""

from __future__ import annotations

from fiil_protocol import LowLatencyMode, MafMode, ToneVolume
from quick_controls import CONTROL_GROUPS, ControlState, menu_model


def test_catalog_uses_verified_protocol_values_and_unique_targets():
    targets = [(option.action, int(option.value)) for group in CONTROL_GROUPS
               for option in group.options]
    assert len(targets) == len(set(targets))
    assert [int(o.value) for o in CONTROL_GROUPS[0].options] == list(MafMode)
    assert [int(o.value) for o in CONTROL_GROUPS[4].options] == list(ToneVolume)


def test_disconnected_snapshot_disables_every_control():
    groups = menu_model(ControlState(connected=False))
    assert all(not option.enabled for group in groups for option in group.options)


def test_menu_model_checks_modes_and_hides_gaia_levels_until_supported():
    groups = menu_model(ControlState(connected=True, maf=1, low_latency=1,
                                     cinema=True, multipoint=False, tone=2))
    maf, latency, cinema, multipoint, tone = groups
    assert [o.checked for o in maf.options] == [False, True, False, False, False]
    # Mate 机型只支持 0/1 两档：不生成会被耳机拒绝的 音乐/视频 项
    assert [int(o.value) for o in latency.options] == [0, 1]
    assert latency.options[1].checked
    assert cinema.options[1].checked and not multipoint.options[1].checked
    assert tone.options[2].checked
    # 读到 2/3 说明是 GAIA 四档机型，此时才补齐全部档位
    gaia = menu_model(ControlState(connected=True, low_latency=2))[1]
    assert [int(o.value) for o in gaia.options] == list(LowLatencyMode)
    assert gaia.options[2].checked


def test_boolean_menu_uses_explicit_on_off_targets_not_blind_toggle():
    groups = menu_model(ControlState(connected=True, cinema=False))
    assert [(o.action, o.value) for o in groups[2].options] == [("cinema", False), ("cinema", True)]
