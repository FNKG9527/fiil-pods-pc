"""LDAC 开关的协议层与菜单模型测试（不连设备）。

字节依据（全部来自逆向，非推测）：
  * 读开关   cid=39(QUERY) cmdId=143(0x8F)  → com.fengeek.main.i.d.c:107 getLdacSwitchStatus
  * 读激活态 cid=39(QUERY) cmdId=142(0x8E)  → c.java:123 getLdacUnlockStatus
  * 写开关   cid=1(SET_PARAM) cmdId=13      → c.java:371 setLdacSwitch
                                              （bluetrum F39AppSettingRequest.SETTING_TYPE_LDAC=13）
实机回包见 docs/evidence/unverified_cmdid_20261025_014438.txt：
  06 27 02 00 03 8F 01 00   → 开关=0（关）
  07 27 02 00 03 8E 01 01   → 已激活
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fiil_protocol import FiilProtocol
from quick_controls import ControlState, menu_model


def hexs(b: bytes) -> str:
    return b.hex(" ").upper()


def test_ldac_switch_frames_match_reverse_engineered_bytes():
    proto = FiilProtocol()
    # seq 从 0 开始滚动，这里只比对除首字节外的固定部分
    assert hexs(proto.request_ldac_switch())[3:] == "27 01 00 02 8F 00"
    assert hexs(proto.request_ldac_unlock())[3:] == "27 01 00 02 8E 00"
    assert hexs(proto.set_ldac_switch(True))[3:] == "01 01 00 03 0D 01 01"
    assert hexs(proto.set_ldac_switch(False))[3:] == "01 01 00 03 0D 01 00"


def test_ldac_switch_set_frame_has_three_byte_length_field():
    """cid=1 且 cmdId!=0 时 lenField = payloadLen + 2 = 3，不能写成 1。"""
    frame = FiilProtocol().set_ldac_switch(True)
    assert len(frame) == 8
    assert frame[4] == 0x03 and frame[5] == 0x0D and frame[6] == 0x01


def test_ldac_menu_disabled_until_activated():
    groups = {g.action: g for g in menu_model(
        ControlState(connected=True, ldac=False, ldac_unlocked=False))}
    ldac = groups["ldac"]
    assert [o.value for o in ldac.options] == [False, True]
    # 没激活时下发开关毫无意义：两个档位都必须禁用，不能让用户以为点了没用是 bug
    assert all(not o.enabled for o in ldac.options)
    assert "激活" in ldac.label


def test_ldac_menu_enabled_and_checked_when_activated():
    groups = {g.action: g for g in menu_model(
        ControlState(connected=True, ldac=True, ldac_unlocked=True))}
    ldac = groups["ldac"]
    assert all(o.enabled for o in ldac.options)
    assert [o.checked for o in ldac.options] == [False, True]
