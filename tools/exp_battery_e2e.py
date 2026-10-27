# -*- coding: utf-8 -*-
"""端到端验证：握手 → 读设备名 → 推断 earType → 读电量 → 按单电池语义显示。

复现 GUI 的真实时序（GET_BATTERY 与 GET_DEVICE_NAME 并发 → 补读），
确认 Key Max 不再显示成「右耳 xx%」。
"""

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import asyncio
import time

from bleak import BleakClient

from fiil_protocol import BatteryState, FiilProtocol, parse_ascii

ADDR = "B0:F1:A3:00:00:01"
WRITE = "0000ff17-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000ff18-0000-1000-8000-00805f9b34fb"


def guess_ear_type(name: str) -> int | None:
    up = (name or "").upper()
    for token, et in (
        ("CC PRO3", 53), ("CC3", 51), ("KEY PRO2", 49), ("GS LINKS", 48),
        ("ATOM", 47), ("KEY MAX", 46), ("GS LITE", 45),
        ("CC PRO2", 39), ("CC NANO", 37), ("KEY PRO", 41), ("KEY", 38),
    ):
        if token in up:
            return et
    return None


async def main() -> None:
    proto = FiilProtocol()
    frames = []

    def on_notify(_s, data):
        frames.extend(proto.feed(bytes(data)))

    async with BleakClient(ADDR, timeout=20.0) as c:
        await c.start_notify(NOTIFY, on_notify)
        await c.write_gatt_char(WRITE, proto.request_unlock(), response=False)
        await asyncio.sleep(0.8)

        async def ask(name: bytes, cmd: int, wait=0.5):
            base = len(frames)
            await c.write_gatt_char(WRITE, name, response=False)
            await asyncio.sleep(wait)
            for f in frames[base:]:
                if f.cid == 39 and f.cmd == cmd and f.payload is not None:
                    return f.payload
            return None

        # 模拟 GUI 的并发：先电量，后名字
        bat_before = await ask(proto.request_battery(), 1)
        name_p = await ask(proto.request_device_name(), 3)
        name = parse_ascii(name_p) if name_p else ""
        et = guess_ear_type(name)

        print(f"设备名       : {name!r}")
        print(f"推断 earType : {et}")
        print(f"电量原始帧   : {bat_before.hex(' ') if bat_before else '(无)'}")

        if bat_before:
            b_old = BatteryState.parse(bat_before)               # 旧逻辑：型号未知
            b_new = BatteryState.parse(bat_before, ear_type=et)  # 新逻辑
            print(f"  型号未知时显示: {b_old.display()}")
            print(f"  按 earType 显示: {b_new.display()}")

        # GUI 现在会补读一次
        await asyncio.sleep(0.3)
        bat_after = await ask(proto.request_battery(), 1)
        if bat_after:
            b2 = BatteryState.parse(bat_after, ear_type=et)
            print(f"补读后显示    : {b2.display()}")
            print(f"  single={b2.single} left={b2.left} right={b2.right} case={b2.case}")

        await c.stop_notify(NOTIFY)


if __name__ == "__main__":
    asyncio.run(main())
