# -*- coding: utf-8 -*-
"""精确测量 MAF SET 的生效延迟。

方法：发 SET 之后，每隔 0.5s 发一次 GET_MAF，记录第一次读到新值的时刻。
同时记录 cid=40 的 NOTIFY 推送时刻（那是最可靠的新值信号）。
不做破坏性写入：测完恢复原值。
"""
from __future__ import annotations

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import asyncio
import time

from bleak import BleakClient

from fiil_protocol import FiilProtocol, MafMode

ADDR = "B0:F1:A3:00:00:01"
W = "0000ff17-0000-1000-8000-00805f9b34fb"
N = "0000ff18-0000-1000-8000-00805f9b34fb"


async def main() -> None:
    p = FiilProtocol()
    frames: list = []

    def cb(_c, data):
        now = time.monotonic()
        for f in p.feed(bytes(data)):
            frames.append((now, f))

    async with BleakClient(ADDR, timeout=20.0) as c:
        await c.start_notify(N, cb)
        await c.write_gatt_char(W, p.request_unlock(), response=False)
        await asyncio.sleep(0.8)

        def get_maf_sync(fs) -> int | None:
            for _t, f in fs:
                if f.cid == 39 and f.cmd == 12 and f.payload:
                    return f.payload[0]
            return None

        async def read_maf(wait=0.45) -> int | None:
            n = len(frames)
            await c.write_gatt_char(W, p.request_maf(), response=False)
            await asyncio.sleep(wait)
            return get_maf_sync(frames[n:])

        orig = await read_maf()
        print(f"原始 MAF = {orig} ({MafMode(orig).label_cn})")

        target = 2 if orig != 2 else 1
        print(f"下发 MAF = {target} ({MafMode(target).label_cn})")

        base = len(frames)
        t0 = time.monotonic()
        await c.write_gatt_char(W, p.set_maf(MafMode(target)), response=False)

        # 每 0.4s 轮询，最多 8 秒
        first_seen = None
        notify_seen = None
        samples = []
        for i in range(20):
            await asyncio.sleep(0.4)
            elapsed = time.monotonic() - t0
            v = await read_maf(0.25)
            samples.append((round(elapsed, 2), v))
            if v == target and first_seen is None:
                first_seen = round(elapsed, 2)
            # 检查 cid=40 推送
            if notify_seen is None:
                for _t, f in frames[base:]:
                    if f.is_notify and f.cmd == 12 and f.payload and f.payload[0] == target:
                        notify_seen = round(_t - t0, 2)
                        break
            if first_seen is not None and notify_seen is not None:
                break

        print("\n轮询序列（秒, 读回值）：")
        for s in samples:
            mark = "  <-- 首次读到新值" if s[1] == target and s[0] == first_seen else ""
            print(f"  {s[0]:>5}s  {s[1]}{mark}")

        print(f"\nGET 首次读到新值：{first_seen}s" if first_seen else "\nGET 8 秒内未读到新值")
        print(f"NOTIFY 首次推送新值：{notify_seen}s" if notify_seen else "NOTIFY 未推送")

        # 恢复
        await c.write_gatt_char(W, p.set_maf(MafMode(orig)), response=False)
        await asyncio.sleep(4.0)
        back = await read_maf(0.5)
        print(f"\n恢复原值 {orig} → 读回 {back}  {'OK' if back == orig else 'FAIL'}")

        await c.stop_notify(N)


if __name__ == "__main__":
    asyncio.run(main())
