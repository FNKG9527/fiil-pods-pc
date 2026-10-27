# -*- coding: utf-8 -*-
"""电量三值归属专项：连续采样 GET_BATTERY，观察 payload[0..2] 变化。

已知回包 `02 27 02 00 05 01 03 <b0> <b1> <b2>`。
要判定：3 个字节是否分别对应 左耳/右耳/充电盒，以及 0x00 与 0xFF 的含义。
"""

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import asyncio
import collections
import time

from bleak import BleakClient

from fiil_protocol import FiilProtocol

ADDR = "B0:F1:A3:00:00:01"
WRITE = "0000ff17-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000ff18-0000-1000-8000-00805f9b34fb"


async def main() -> None:
    proto = FiilProtocol()
    frames = []

    def on_notify(_s, data):
        frames.extend(proto.feed(bytes(data)))

    async with BleakClient(ADDR, timeout=20.0) as c:
        await c.start_notify(NOTIFY, on_notify)
        await c.write_gatt_char(WRITE, proto.request_unlock(), response=False)
        await asyncio.sleep(0.8)

        samples = []
        print("idx  raw-payload        b0   b1   b2   (hex)")
        print("-" * 54)
        for i in range(20):
            base = len(frames)
            await c.write_gatt_char(WRITE, proto.request_battery(), response=False)
            await asyncio.sleep(0.35)
            got = None
            for f in frames[base:]:
                if f.cid == 39 and f.cmd == 1 and f.payload:
                    got = f.payload
                    break
            if got:
                p = list(got)
                samples.append(tuple(p))
                print(f"{i:<4} {str(p):<18} {p[0]:<4} {p[-2] if len(p)>=2 else '-':<4} "
                      f"{p[-1]:<4} {got.hex(' ')}")
            else:
                print(f"{i:<4} (无回包)")
            await asyncio.sleep(0.25)

        # 同时记录 cid=40 主动推送里的电量类帧
        print("\n=== 本次会话收到的全部 cid=40 NOTIFY ===")
        for f in frames:
            if f.cid == 40:
                print(f"  cmd={f.cmd} payload={f.payload.hex(' ')}")

        await c.stop_notify(NOTIFY)

    print("\n=== 统计 ===")
    if samples:
        print(f"样本数 {len(samples)}，去重后：")
        for combo, n in collections.Counter(samples).most_common():
            print(f"  {combo}  ×{n}")
        b0 = [s[0] for s in samples]
        b1 = [s[-2] for s in samples if len(s) >= 2]
        b2 = [s[-1] for s in samples]
        print(f"b0 取值域: {sorted(set(b0))}")
        print(f"b1 取值域: {sorted(set(b1))}")
        print(f"b2 取值域: {sorted(set(b2))}")


if __name__ == "__main__":
    asyncio.run(main())
