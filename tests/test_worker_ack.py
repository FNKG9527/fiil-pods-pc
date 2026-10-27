# -*- coding: utf-8 -*-
"""离线验证 BleWorker 的 SET-ACK 归属与握手自检逻辑（不需要真机）。

覆盖两个真实缺陷的回归：
1. GET 的 cid 也是 39 —— 发 GET 时不能把 _pending_set_name 覆盖掉，
   否则 SET 之后紧跟的校验 GET 会让稍后到达的 ACK 失去归属。
2. SET 的 ACK 必须能正确判定成功/失败（Mate 机型拒绝低延迟 2/3 就是靠这个）。

运行：python -X utf8 tests/test_worker_ack.py   或   pytest tests/test_worker_ack.py
"""
from __future__ import annotations

import asyncio
import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ble_worker import BleWorker, Event  # noqa: E402
from fiil_protocol import FiilProtocol, drain  # noqa: E402

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


class FakeClient:
    is_connected = True

    def __init__(self) -> None:
        self.written: list[bytes] = []

    async def write_gatt_char(self, _uuid, data, response=False):  # noqa: ARG002, FBT002
        self.written.append(bytes(data))


def new_worker():
    evt_q: queue.Queue[Event] = queue.Queue()
    cmd_q: queue.Queue[Event] = queue.Queue()
    w = BleWorker(evt_q, cmd_q)
    w.client = FakeClient()
    events: list[Event] = []
    w._emit = lambda kind, text="", payload=None: events.append(Event(kind, text, payload))
    return w, events


def feed(w: BleWorker, raw_hex: str) -> None:
    """把一段 hex 喂进 worker 的收包路径（和真实 notify 一样走 drain+_emit_frame）。"""
    frames, _rest = drain(bytes.fromhex(raw_hex.replace(" ", "")))
    for f in frames:
        w._emit_frame(f)


def main() -> int:
    print("1. SET 之后紧跟 GET，ACK 归属不能丢")
    w, ev = new_worker()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    p = FiilProtocol()
    # 下发 SET_LOW_LATENCY=2（Mate 会拒绝）
    loop.run_until_complete(w._send_raw("SET_LOW_LATENCY=2(音乐)", p.set_low_latency(2)))
    check("下发后 pending 记下了 cid=37", w._pending_set_cid, 37)
    check("下发后 pending 记下了名字", w._pending_set_name, "SET_LOW_LATENCY=2(音乐)")

    # 紧接着发一条校验 GET —— 这是 GUI 的固定动作
    loop.run_until_complete(w._send_raw("GET_LOW_LATENCY(校验)", p.request_low_latency()))
    check("GET 不清掉 pending 名字", w._pending_set_name, "SET_LOW_LATENCY=2(音乐)")

    # 耳机回 ACK（拒绝）
    feed(w, "052502000101")
    acks = [e for e in ev if e.kind == "setack"]
    check("收到 1 条 setack", len(acks), 1)
    check("判定为被拒绝", (acks[0].payload or {}).get("ok"), False)
    check("归属到正确的 SET", (acks[0].payload or {}).get("name"), "SET_LOW_LATENCY=2(音乐)")
    check("ACK 处理后 pending 被清空", w._pending_set_cid, None)

    print("\n2. SET 成功时的判定")
    w2, ev2 = new_worker()
    loop.run_until_complete(w2._send_raw("SET_TONE=1", FiilProtocol().set_tone_volume(1)))
    feed(w2, "0001020003030100")
    acks2 = [e for e in ev2 if e.kind == "setack"]
    check("收到 setack", len(acks2), 1)
    check("判定为成功", (acks2[0].payload or {}).get("ok"), True)

    print("\n3. setUnlock（cid=39）不能被当成 SET")
    w3, _ev3 = new_worker()
    loop.run_until_complete(w3._send_raw("setUnlock（连接握手）", FiilProtocol().request_unlock()))
    check("setUnlock 不设置 pending", w3._pending_set_cid, None)

    print("\n4. 握手自检标记：三条状态未回时保持 False，回了置 True")
    w4, ev4 = new_worker()
    check("初始 _got_ll", w4._got_ll, False)
    feed(w4, "0327020003080101")          # GET_LOW_LATENCY 回包
    check("收到 LL 后 _got_ll", w4._got_ll, True)
    check("_got_mp 仍为 False", w4._got_mp, False)
    feed(w4, "0527020003800101")          # GET_CHANGLAN 回包
    check("收到 MP 后 _got_mp", w4._got_mp, True)
    feed(w4, "0127020003820102")          # GET_TONE 回包（payload 01 02 → 档位 2）
    check("收到 TONE 后 _got_tone", w4._got_tone, True)
    tones = [e for e in ev4 if e.kind == "tone"]
    check("tone 事件的值", (tones[0].payload or {}).get("value"), 2)

    print()
    total = PASS + FAIL
    print(f"结果：{PASS}/{total} 通过" + ("" if FAIL == 0 else f"，{FAIL} 失败"))
    loop.close()
    return 0 if FAIL == 0 else 1


def test_all() -> None:
    """pytest 入口。"""
    assert main() == 0, f"{FAIL} 项失败"


if __name__ == "__main__":
    sys.exit(main())
