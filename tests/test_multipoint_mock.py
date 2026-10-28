"""畅连（多点）切换 → 断链 → 自动重连 的离线仿真回归。

耳机此刻不在广播/不可连，无法跑 test_multipoint_reconnect.py 的实机版。
本脚本用假的 BleakClient 完整走一遍状态机，保证：

  1. SET_MULTIPOINT 下发**之前**就置了 _reconnect_after_multipoint（时序正确）；
  2. 断线回调走「切换中」分支，不报「连接丢失」；
  3. _reconnect_loop 按退避序列重试，耳机恢复后能连上；
  4. 重连成功后重新 setUnlock 并重读全部状态；
  5. 用户主动 disconnect 会取消正在跑的重连（不会自己连回去）；
  6. 非畅连的 SET（如低延迟）不会误触发重连。

运行：python -X utf8 tests/test_multipoint_mock.py   或   pytest tests/test_multipoint_mock.py
"""
from __future__ import annotations

import asyncio
import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ble_connect as bc  # noqa: E402
import ble_worker as bw  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


class FakeClient:
    """假 BleakClient：记录写入了什么，可控连接/断开。"""

    registry: list["FakeClient"] = []

    def __init__(self, address, timeout=20.0, disconnected_callback=None):
        self.address = address
        self.timeout = timeout
        self.disconnected_callback = disconnected_callback
        self.is_connected = False
        self.writes: list[tuple[str, bytes]] = []
        self.notify_started = False
        FakeClient.registry.append(self)

    async def connect(self):
        if FakeClient.connect_should_fail:
            raise TimeoutError("mock connect timeout")
        self.is_connected = True

    async def start_notify(self, uuid, cb):
        self.notify_started = True

    async def stop_notify(self, uuid):
        self.notify_started = False

    async def disconnect(self):
        self.is_connected = False
        if self.disconnected_callback:
            self.disconnected_callback(self)

    async def write_gatt_char(self, uuid, frame, response=False):
        self.writes.append((uuid, bytes(frame)))

    # 模拟耳机主动踢线
    def kick(self):
        self.is_connected = False
        if self.disconnected_callback:
            self.disconnected_callback(self)


FakeClient.connect_should_fail = False


def install_fakes() -> None:
    # 连接已改走 ble_connect.connect_fast，工厂函数在那里定义，两个都要打补丁
    bw.BleakClient = FakeClient  # type: ignore[assignment]
    bc._default_client_factory = (  # type: ignore[assignment]
        lambda target, timeout, callback: FakeClient(
            getattr(target, "address", target), timeout=timeout, disconnected_callback=callback
        )
    )
    bc._default_scanner_factory = lambda cb: FakeScanner(cb)  # type: ignore[assignment]

    # 把退避缩短到毫秒级，否则测试要跑 30 秒
    _real_sleep = asyncio.sleep

    async def fast_sleep(sec):
        await _real_sleep(min(sec, 0.01))

    bw.asyncio.sleep = fast_sleep  # type: ignore[assignment]


class FakeScanner:
    """永远扫不到目标的扫描器：连接失败场景要快速走完，不能真等 8 秒。"""

    def __init__(self, detection_callback) -> None:
        self.detection_callback = detection_callback

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


def make_worker() -> tuple[bw.BleWorker, list[tuple[str, str]]]:
    evq: queue.Queue = queue.Queue()
    cmdq: queue.Queue = queue.Queue()
    w = bw.BleWorker(evq, cmdq)
    # 扫描超时压到毫秒级：连接失败场景要走完「扫描寻找」分支，不能真等 8 秒
    w.scan_timeout = 0.05
    captured: list[tuple[str, str, object]] = []
    orig = w._emit

    def cap(kind, text="", payload=None):
        captured.append((kind, text, payload))
        orig(kind, text, payload)

    w._emit = cap  # type: ignore[assignment]
    w._captured = captured  # type: ignore[attr-defined]
    return w, captured


async def scenario_normal(w, captured) -> None:
    print("\n[场景 1] 畅连 SET → 耳机踢线 → 自动重连成功")
    w.loop = asyncio.get_running_loop()
    w.address = "B0:F1:A3:00:00:01"

    FakeClient.registry.clear()
    FakeClient.connect_should_fail = False

    # 先建立一条「已连接」的链路
    w.client = FakeClient(w.address, disconnected_callback=w._on_ble_disconnected)
    w.client.is_connected = True
    w.protocol = bw.FiilProtocol()
    w._closing = False

    # 下发畅连
    await w._handle(bw.Command("set", ("set_multipoint", True, "SET_MULTIPOINT=1")))
    check("下发畅连前置了重连标志", w._reconnect_after_multipoint is True)

    # 耳机踢线
    w.client.kick()

    texts = [t for _, t, _ in captured]
    check("断线回落到了「切换中」分支",
          any("切换中" in t for t in texts), str(texts[-4:]))
    check("没有报「连接丢失」", not any("连接丢失" in t for t in texts))
    check("重连任务已创建", w._reconnect_task is not None)
    check("标志已被消费（不会重复触发）", w._reconnect_after_multipoint is False)

    # 等重连任务跑完
    await asyncio.wait_for(w._reconnect_task, timeout=10)

    texts = [t for _, t, _ in captured]
    check("自动重连成功", any("已自动重连" in t for t in texts), str(texts[-6:]))
    check("重连后客户端已就位", w.client is not None and w.client.is_connected)

    # 重连后的握手必须重发 setUnlock
    writes = [f for _, f in (w.client.writes if w.client else [])]
    unlock = bytes.fromhex("0027010004FF020234")
    check("重连后发了 setUnlock", unlock in writes,
          f"writes={[f.hex(' ') for f in writes[:3]]}")
    others = [f for f in writes if f != unlock]
    check("重连后重读了状态（≥5 条查询）", len(others) >= 5, f"count={len(others)}")


async def scenario_user_disconnect(w, captured) -> None:
    print("\n[场景 2] 重连进行中用户主动断开 → 重连被取消")
    w.loop = asyncio.get_running_loop()
    w.address = "B0:F1:A3:00:00:01"
    captured.clear()

    FakeClient.registry.clear()
    FakeClient.connect_should_fail = True  # 让重连一直失败，保持运行

    w.client = FakeClient(w.address, disconnected_callback=w._on_ble_disconnected)
    w.client.is_connected = True
    w._closing = False
    w._reconnect_after_multipoint = True
    w.client.kick()
    task = w._reconnect_task
    await asyncio.sleep(0.05)
    check("重连任务在跑", task is not None and not task.done())

    await w._close_client()
    await asyncio.sleep(0.05)
    check("用户断开后重连标志被清", w._reconnect_after_multipoint is False)
    check("用户断开后重连任务被取消", task is not None and task.cancelled() or task.done())


async def scenario_other_set(w, captured) -> None:
    print("\n[场景 3] 非畅连 SET 不触发重连")
    captured.clear()
    w2, cap2 = make_worker()
    w2.loop = asyncio.get_running_loop()
    w2.client = FakeClient("addr", disconnected_callback=w2._on_ble_disconnected)
    w2.client.is_connected = True
    w2._closing = False

    await w2._handle(bw.Command("set", ("set_low_latency", 1, "SET_LOW_LATENCY=1")))
    check("低延迟 SET 不置重连标志", w2._reconnect_after_multipoint is False)


async def scenario_retry_exhausted(w, captured) -> None:
    print("\n[场景 4] 重连彻底失败 → 给出可操作提示")
    captured.clear()
    w3, cap3 = make_worker()
    w3.loop = asyncio.get_running_loop()
    w3.address = "B0:F1:A3:00:00:01"
    FakeClient.connect_should_fail = True
    w3.client = FakeClient(w3.address, disconnected_callback=w3._on_ble_disconnected)
    w3.client.is_connected = True
    w3._closing = False
    w3._reconnect_after_multipoint = True
    w3.client.kick()
    await asyncio.wait_for(w3._reconnect_task, timeout=15)

    texts = [t for _, t, _ in cap3]
    check("重试了 5 次", sum(1 for t in texts if "次重连尝试" in t) == 5,
          str([t for t in texts if "重连尝试" in t]))
    check("失败后给了用户可操作的提示",
          any("请稍后手动点" in t for t in texts), str(texts[-3:]))


async def main() -> int:
    install_fakes()

    bw2, cap2 = make_worker()
    await scenario_normal(bw2, cap2)

    bw3, cap3 = make_worker()
    await scenario_user_disconnect(bw3, cap3)

    await scenario_other_set(bw2, cap2)

    bw4, cap4 = make_worker()
    await scenario_retry_exhausted(bw4, cap4)

    print("\n" + "=" * 60)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


def test_all() -> None:
    """pytest 入口。"""
    assert asyncio.run(main()) == 0, f"{FAIL} 项失败"


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
