"""畅连（多点）切换后自动重连的实机验证。

验证目标（对应 PROTOCOL_PC.md §9.2）：
  1. 下发 SET_MULTIPOINT 后耳机确实断链；
  2. 程序不再报「连接丢失」，而是报「切换中」；
  3. 退避重连自动跑起来，并在耳机重新可连后成功连上；
  4. 重连后 setUnlock + 全量状态重读都跑完，读到的是切换后的新值。

用法：
    python -X utf8 test_multipoint_reconnect.py [地址]

不传地址时使用 known_devices.json 里记录的最后一次成功连接地址。
测试结束会把畅连恢复到原始值（除非中途失败）。
"""
from __future__ import annotations

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import queue
import sys
import time
from pathlib import Path

from ble_worker import _KNOWN_DEVICES_PATH, BleWorker, Command, Event

DEFAULT_ADDR = "B0:F1:A3:00:00:01"


def load_addr() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1].upper()
    try:
        data = json.loads(Path(_KNOWN_DEVICES_PATH).read_text(encoding="utf-8"))
        for addr in data:
            return addr.upper()
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_ADDR


class Recorder:
    """把 worker 的事件按时间顺序记下来，方便事后断言。"""

    def __init__(self) -> None:
        self.events: list[tuple[float, Event]] = []

    def add(self, ev: Event) -> None:
        self.events.append((time.time(), ev))

    def texts(self) -> list[str]:
        return [e.text for _, e in self.events]

    def find(self, needle: str) -> list[Event]:
        return [e for _, e in self.events if needle in (e.text or "")]

    def kinds(self, kind: str) -> list[Event]:
        return [e for _, e in self.events if e.kind == kind]


def drain(rec: Recorder, evq: "queue.Queue[Event]", seconds: float, stop_on=None) -> None:
    """把事件队列里的事件全部转存到 recorder，持续 seconds 秒。"""
    end = time.time() + seconds
    while time.time() < end:
        try:
            ev = evq.get(timeout=0.2)
        except queue.Empty:
            continue
        rec.add(ev)
        print(f"  {ev.clock}  [{ev.kind}] {ev.text}")
        if stop_on and stop_on(ev):
            return


def main() -> int:
    addr = load_addr()
    print(f"目标设备：{addr}")
    print("=" * 72)

    evq: queue.Queue[Event] = queue.Queue()
    cmdq: queue.Queue[Command] = queue.Queue()
    w = BleWorker(evq, cmdq)
    w.start()
    rec = Recorder()

    try:
        # ---- 1. 连接 ----
        print("\n[1] 连接耳机…")
        w.submit(Command("connect", addr))
        drain(rec, evq, 40, stop_on=lambda e: e.kind == "conn" and "已连接" in e.text)
        if not rec.find("已连接"):
            print("\n✗ 连接失败，测试中止")
            return 1

        # 等握手把状态读全
        print("\n[2] 等待握手与状态读取…")
        drain(rec, evq, 6)

        mp_events = [e for e in rec.kinds("multipoint")]
        original = bool((mp_events[-1].payload or {}).get("on")) if mp_events else None
        print(f"    当前畅连 = {original}")

        # ---- 2. 切换畅连 ----
        target = not original
        print(f"\n[3] 下发 SET_MULTIPOINT={1 if target else 0}（预期：耳机立刻断链）")
        drain(rec, evq, 30, stop_on=lambda e: e.kind == "conn" and "切换中" in e.text)
        switching = rec.find("切换中")
        if switching:
            print(f"    ✓ 判定为「切换中」而非「连接丢失」：{switching[-1].text}")
        else:
            print("    ✗ 没有出现「切换中」事件")

        lost = [t for t in rec.texts() if "连接丢失" in t]
        print(f"    {'✓' if not lost else '✗'} 「连接丢失」错误提示数量 = {len(lost)}")

        # ---- 3. 等自动重连 ----
        print("\n[4] 等待自动重连（退避 2/3/5/8/12 秒，最长约 30 秒）…")
        drain(rec, evq, 45, stop_on=lambda e: "自动重连" in (e.text or ""))
        ok = rec.find("已自动重连")
        print(f"    {'✓' if ok else '✗'} 自动重连{'成功' if ok else '失败'}")

        if ok:
            # 重连后的握手会重读状态；再等一会儿
            drain(rec, evq, 8)
            mp_after = [e for e in rec.kinds("multipoint")]
            new_val = bool((mp_after[-1].payload or {}).get("on")) if mp_after else None
            print(f"\n[5] 重连后读回畅连 = {new_val}（期望 {target}）")
            print(f"    {'✓ 一致' if new_val == target else '✗ 不一致'}")
            print(f"    握手日志：{'✓ 有 setUnlock' if rec.find('setUnlock') else '✗ 缺 setUnlock'}")

            # ---- 4. 恢复原值 ----
            print(f"\n[6] 恢复畅连到原值 {original}…")
            w.submit(Command("set", ("set_multipoint", bool(original),
                                     f"SET_MULTIPOINT={1 if original else 0}（恢复）")))
            drain(rec, evq, 45, stop_on=lambda e: "自动重连" in (e.text or ""))
            print("    恢复流程已跑完（同样会经历一次断链+重连）")

        # ---- 汇总 ----
        print("\n" + "=" * 72)
        checks = {
            "判为切换中（不报连接丢失）": bool(switching) and not lost,
            "自动重连成功": bool(ok),
        }
        for name, passed in checks.items():
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        return 0 if all(checks.values()) else 1

    finally:
        w.shutdown()
        w.join(timeout=15)


if __name__ == "__main__":
    raise SystemExit(main())
