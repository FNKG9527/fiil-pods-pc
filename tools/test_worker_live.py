# -*- coding: utf-8 -*-
"""端到端验证 BleWorker：真机连接 → 握手 → 状态回读 → 主动断开。

不启动 GUI，直接用 BleWorker + 事件队列，验证：
1. conn 事件 connected=True 发出
2. setUnlock 之后 GET 全部有回包
3. battery 事件带正确的 left/right/case
4. maf / cinema 事件能回填
5. 主动 disconnect 只上报一次 conn(False)，不被 disconnected_callback 重复触发
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

    events: list[Event] = []

    def drain(secs: float) -> list[Event]:
        end = time.time() + secs
        got = []
        while time.time() < end:
            try:
                e = evt_q.get(timeout=0.1)
            except queue.Empty:
                continue
            got.append(e)
            events.append(e)
        return got

    cmd_q.put(Command("connect", ADDR))
    got = drain(9.0)

    conn_true = [e for e in got if e.kind == "conn" and (e.payload or {}).get("connected")]
    # 注意：_connect() 开头会先 _close_client()，那一步会发一条 conn(False)。
    # 所以连接阶段的 conn(False) 数量应为 1（初始清理），不能是 2+（那才说明真掉线）。
    conn_false = [e for e in got if e.kind == "conn" and not (e.payload or {}).get("connected")]
    batt = [e for e in got if e.kind == "battery"]
    maf = [e for e in got if e.kind == "maf"]
    cinema = [e for e in got if e.kind == "cinema"]
    errs = [e for e in got if e.kind == "error"]

    print(f"conn(True)  事件数 = {len(conn_true)}  {'OK' if len(conn_true) == 1 else 'FAIL'}")
    print(f"conn(False) 事件数 = {len(conn_false)}  "
          f"{'OK（仅为 _connect 开头的初始清理）' if len(conn_false) <= 1 else 'FAIL（出现中途掉线）'}")
    print(f"battery     事件数 = {len(batt)}  {'OK' if batt else 'FAIL'}")
    if batt:
        b = batt[0].payload
        # Key Max = F046 单电池：正确结果是 single 有值、left/right/case 全 None，
        # 且文本形如「耳机 77%」。若出现「右 77%」说明单电池语义没生效。
        print(f"   single={b.single} left={b.left} right={b.right} case={b.case}  文本：{batt[0].text}")
        single_ok = b.single is not None and b.right is None and b.left is None
        print(f"   单电池语义 {'OK' if single_ok else 'FAIL（被当成三价值解析）'}")
    print(f"maf         事件数 = {len(maf)}  {'OK' if maf else 'FAIL'}")
    if maf:
        print(f"   {maf[0].text}  payload={maf[0].payload}")
    print(f"cinema      事件数 = {len(cinema)}  {'OK' if cinema else 'FAIL'}")
    print(f"error       事件数 = {len(errs)}  {'OK' if not errs else 'FAIL'}")
    for e in errs:
        print("   !!", e.text)

    rx = [e for e in got if e.kind == "rx"]
    print(f"rx 原始帧数 = {len(rx)}")

    # 主动断开
    cmd_q.put(Command("disconnect"))
    got2 = drain(4.0)
    cf2 = [e for e in got2 if e.kind == "conn" and not (e.payload or {}).get("connected")]
    print(f"主动断开后 conn(False) 事件数 = {len(cf2)}  {'OK（无重复上报）' if len(cf2) == 1 else 'FAIL'}")

    w.shutdown()
    ok = (len(conn_true) == 1 and len(conn_false) <= 1
          and batt and maf and cinema and not errs and len(cf2) == 1)
    print("RESULT:", "ALL OK" if ok else "HAS FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
