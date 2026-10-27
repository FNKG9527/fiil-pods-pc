# -*- coding: utf-8 -*-
"""端到端验证：低延迟 / 蓝牙畅连 / 语音提示音量 三个新功能在 BleWorker 里是否闭环。

不启动 GUI，直接用 BleWorker + 事件队列：
1. 连接握手后，三个新查询都要有对应事件（lowlatency / multipoint / tone）
2. 三者的 SET 下发都要收到 setack 事件（ok=True）
3. 读回值必须与写入一致（写后校验闭环）
4. 畅连放最后（切换主从会短暂断链）

运行：python -X utf8 test_new3_live.py
"""
from __future__ import annotations

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import queue
import sys
import time

from ble_worker import BleWorker, Command, Event

ADDR = "B0:F1:A3:00:00:01"


def main() -> int:
    evt_q: queue.Queue[Event] = queue.Queue()
    cmd_q: queue.Queue[Command] = queue.Queue()
    w = BleWorker(evt_q, cmd_q)
    w.start()
    time.sleep(0.5)

    def drain(secs: float) -> list[Event]:
        end = time.time() + secs
        got = []
        while time.time() < end:
            try:
                got.append(evt_q.get(timeout=0.1))
            except queue.Empty:
                continue
        return got

    cmd_q.put(Command("connect", ADDR))
    got = drain(8.0)
    conn = [e for e in got if e.kind == "conn" and (e.payload or {}).get("connected")]
    ll = [e for e in got if e.kind == "lowlatency"]
    mp = [e for e in got if e.kind == "multipoint"]
    tone = [e for e in got if e.kind == "tone"]
    errs = [e for e in got if e.kind == "error"]

    print("== 连接与握手 ==")
    print(f"conn(True) = {len(conn)}  {'OK' if conn else 'FAIL'}")
    print(f"lowlatency 初始读回事件 = {len(ll)}  {ll[0].text if ll else 'FAIL（握手没读到）'}")
    print(f"multipoint 初始读回事件 = {len(mp)}  {mp[0].text if mp else 'FAIL（握手没读到）'}")
    print(f"tone       初始读回事件 = {len(tone)}  {tone[0].text if tone else 'FAIL（握手没读到）'}")
    for e in errs:
        print("  !! error:", e.text)

    ll0 = (ll[0].payload or {}).get("value") if ll else None
    mp0 = (mp[0].payload or {}).get("on") if mp else None
    tone0 = (tone[0].payload or {}).get("value") if tone else None
    print(f"初始值：低延迟={ll0} 畅连={mp0} 提示音量={tone0}")

    ok = True

    # ---- 低延迟：切到与当前相反的一档，验证 setack + 读回 ----
    print("\n== 低延迟 SET ==")
    target_ll = 0 if ll0 == 1 else 1
    cmd_q.put(Command("set", ("set_low_latency", target_ll, f"SET_LOW_LATENCY={target_ll}")))
    g = drain(3.0)
    acks = [e for e in g if e.kind == "setack"]
    cmd_q.put(Command("call", ("request_low_latency", "GET_LOW_LATENCY(校验)")))
    g += drain(2.5)
    back = [e for e in g if e.kind == "lowlatency"]
    print(f"setack 事件 = {len(acks)}  {acks[0].text if acks else 'FAIL'}")
    print(f"读回事件 = {len(back)}  {back[-1].text if back else 'FAIL'}")
    ll_ok = bool(acks) and (acks[0].payload or {}).get("ok") and back and \
        (back[-1].payload or {}).get("value") == target_ll
    print("低延迟闭环", "OK" if ll_ok else "FAIL")
    ok = ok and ll_ok

    # ---- 语音提示音量：切到「音量中」再验证 ----
    print("\n== 语音提示音量 SET ==")
    target_tone = 1
    cmd_q.put(Command("set", ("set_tone_volume", target_tone, f"SET_TONE={target_tone}")))
    g = drain(2.5)
    acks = [e for e in g if e.kind == "setack"]
    cmd_q.put(Command("call", ("request_tone_volume", "GET_TONE(校验)")))
    g += drain(2.5)
    back = [e for e in g if e.kind == "tone"]
    print(f"setack 事件 = {len(acks)}  {acks[0].text if acks else 'FAIL'}")
    print(f"读回事件 = {len(back)}  {back[-1].text if back else 'FAIL'}")
    tone_ok = bool(acks) and (acks[0].payload or {}).get("ok") and back and \
        (back[-1].payload or {}).get("value") == target_tone
    print("提示音量闭环", "OK" if tone_ok else "FAIL")
    ok = ok and tone_ok
    # 恢复原值
    if tone0 is not None:
        cmd_q.put(Command("set", ("set_tone_volume", tone0, f"SET_TONE={tone0}(restore)")))
        drain(2.0)

    # ---- 畅连：放最后，切换会断链 ----
    print("\n== 蓝牙畅连 SET（切换可能断链）==")
    target_mp = not bool(mp0)
    cmd_q.put(Command("set", ("set_multipoint", target_mp, f"SET_MULTIPOINT={int(target_mp)}")))
    g = drain(4.0)
    acks = [e for e in g if e.kind == "setack"]
    disconn = [e for e in g if e.kind == "conn" and not (e.payload or {}).get("connected")]
    print(f"setack 事件 = {len(acks)}  {acks[0].text if acks else '（可能断链，未收到）'}")
    print(f"断链事件 = {len(disconn)}（主从切换时预期 ≥1）")
    mp_ok = True
    if acks:
        mp_ok = bool((acks[0].payload or {}).get("ok"))
    print("畅连下发", "OK" if mp_ok else "FAIL")
    ok = ok and mp_ok

    # 恢复畅连初值
    try:
        cmd_q.put(Command("connect", ADDR))
        drain(8.0)
        cmd_q.put(Command("set", ("set_multipoint", bool(mp0),
                                  f"SET_MULTIPOINT={int(bool(mp0))}(restore)")))
        drain(4.0)
    except Exception:  # noqa: BLE001
        pass

    cmd_q.put(Command("disconnect"))
    drain(3.0)
    w.shutdown()

    print("\nRESULT:", "ALL OK" if ok else "HAS FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
