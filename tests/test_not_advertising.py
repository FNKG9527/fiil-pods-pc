# -*- coding: utf-8 -*-
"""离线验证「耳机不在广播」这条失败路径的事件流（不需要真机、不建 Tk）。

覆盖三个真实缺陷的回归：
1. 扫不到目标后还要走「地址兜底」，把同一轮扫描再跑一遍 → 实测 28 秒才报错。
   现在 connect_fast 直接收工并带 reason="not_advertising"。
2. 用户看到的只有一句「TimeoutError」，完全不知道该做什么。
3. 状态胶囊（标题栏那枚小标签）宽度有限，长文案会把工具栏挤变形，
   所以胶囊里必须是短句，可操作指引只能进状态栏和日志页。

运行：python -X utf8 tests/test_not_advertising.py
"""
from __future__ import annotations

import asyncio
import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ble_worker as bw  # noqa: E402
from ble_connect import ConnectAttempt, ConnectResult  # noqa: E402
from ble_worker import BleWorker, Event  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}: got={got!r} want={want!r}")


def new_worker():
    evt_q: queue.Queue[Event] = queue.Queue()
    cmd_q: queue.Queue[Event] = queue.Queue()
    w = BleWorker(evt_q, cmd_q)
    events: list[Event] = []
    w._emit = lambda kind, text="", payload=None: events.append(Event(kind, text, payload))
    return w, events


def failing_result(reason: str = "not_advertising", error: str = "") -> ConnectResult:
    return ConnectResult(
        attempts=[
            ConnectAttempt("地址直连", 0.14, False, "BleakDeviceNotFoundError"),
            ConnectAttempt("扫描寻找", 8.05, False, "8 秒内未扫到该地址"),
        ],
        error=error or (
            "未发现 B0:F1:A3:A8:7A:C8 的 BLE 广播。FIIL 双模耳机被经典蓝牙占用"
            "（正在放音频）时会停止广播，此时按地址直连也会失败。请先把耳机从 "
            "Windows 音频设备里断开（或关掉正在播放的声音），等 BLE 重新广播后再点连接。"
        ),
        reason=reason,
    )


def run_connect(w: BleWorker, result: ConnectResult, address: str = "B0:F1:A3:A8:7A:C8"):
    """把 connect_fast 换成固定结果，跑真实的 _connect_inner 失败分支。"""
    original = bw.connect_fast
    calls: list[str] = []

    async def fake(address_, **kwargs):  # noqa: ANN001, ANN003
        calls.append(address_)
        on_stage = kwargs.get("on_stage")
        if on_stage:
            on_stage("地址直连")
            on_stage("扫描寻找")
        return result

    bw.connect_fast = fake
    try:
        ok = asyncio.run(w._connect_inner(address, keep_state=False))
    finally:
        bw.connect_fast = original
    return ok, calls


def main() -> int:
    print("1. not_advertising：胶囊短、状态栏/日志带可操作指引")
    w, events = new_worker()
    ok, _calls = run_connect(w, failing_result())
    check("返回 False", ok, False)

    errs = [e.text for e in events if e.kind == "error"]
    check("有 error 事件", len(errs), 1)
    check("error 里说了“广播”", "广播" in errs[0], True)
    check("error 里给了动作（断开音频）", "断开" in errs[0] or "经典蓝牙" in errs[0], True)

    states = [e.text for e in events if e.kind == "state"]
    capsule = [s for s in states if "广播" in s]
    check("胶囊只发一次", len(capsule), 1)
    check("胶囊是短句", capsule[0], "未连接 · 耳机不在广播")
    check("胶囊不超过 20 字", len(capsule[0]) <= 20, True)

    # 关键：不能再补发一句「未连接」，否则会盖掉刚设好的短句，
    # 用户就又看不到“耳机不在广播”这个原因了。
    check("不补发覆盖用的“未连接”", "未连接" in states, False)
    conns = [(e.text, (e.payload or {}).get("connected")) for e in events if e.kind == "conn"]
    check("有 conn 断连事件", conns, [("未连接", False)])

    logs = "\n".join(e.text for e in events if e.kind == "log")
    check("日志有耗时明细", "合计" in logs, True)
    check("日志有操作步骤", "系统声音里断开" in logs, True)

    print("\n2. 其他失败原因：仍发普通“未连接”，不冒充“不在广播”")
    w2, events2 = new_worker()
    run_connect(w2, failing_result(reason="connect_failed", error="TimeoutError: "))
    states2 = [e.text for e in events2 if e.kind == "state"]
    check("没有“不在广播”短句", any("广播" in s for s in states2), False)
    check("有普通未连接", "未连接" in states2, True)
    check("error 原样透出", [e.text for e in events2 if e.kind == "error"],
          ["连接失败：TimeoutError: "])

    print("\n3. keep_state=True（畅连重连）：不改状态胶囊")
    w3, events3 = new_worker()
    bw_original = bw.connect_fast

    async def fake3(address_, **kwargs):  # noqa: ANN001, ANN003
        return failing_result()

    bw.connect_fast = fake3
    try:
        asyncio.run(w3._connect_inner("B0:F1:A3:A8:7A:C8", keep_state=True))
    finally:
        bw.connect_fast = bw_original
    kinds3 = [e.kind for e in events3]
    check("keep_state 下不发 conn", "conn" in kinds3, False)

    print()
    total = PASS + FAIL
    print(f"结果：{PASS}/{total} 通过" + ("" if FAIL == 0 else f"，{FAIL} 失败"))
    return 0 if FAIL == 0 else 1


def test_not_advertising() -> None:
    """pytest 入口。"""
    assert main() == 0, f"{FAIL} 项失败"


if __name__ == "__main__":
    sys.exit(main())
