# -*- coding: utf-8 -*-
"""MAF 全五态写入验证：逐一 SET 0..4，读回确认，最后恢复原值。

目标要求 MAF 五态（降噪/自适应/风中/监听/关闭）。
此前只有 0/1/2 有实机证据，本脚本专门补 3（兼听）与 4（风中）。
"""

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import asyncio
import time

from bleak import BleakClient

from fiil_protocol import FiilProtocol

ADDR = "B0:F1:A3:00:00:01"
WRITE = "0000ff17-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000ff18-0000-1000-8000-00805f9b34fb"


async def main() -> None:
    proto = FiilProtocol()
    frames = []

    def on_notify(_sender, data):
        now = time.monotonic()
        for f in proto.feed(bytes(data)):
            frames.append((now, f))

    async with BleakClient(ADDR, timeout=20.0) as client:
        await client.start_notify(NOTIFY, on_notify)
        await client.write_gatt_char(WRITE, proto.request_unlock(), response=False)
        await asyncio.sleep(0.8)

        def last_value(cmd: int, since: int):
            for _t, f in reversed(frames[since:]):
                if f.cid == 39 and f.cmd == cmd and f.payload:
                    return f.payload[0]
            return None

        async def get_maf() -> int | None:
            base = len(frames)
            await client.write_gatt_char(WRITE, proto.request_maf(), response=False)
            await asyncio.sleep(0.45)
            return last_value(12, base)

        original = await get_maf()
        print(f"原始 MAF = {original}\n")
        print(f"{'写入':<6} {'ACK':<6} {'@0.6s':<8} {'@2.0s':<8} {'@3.0s':<8} 判定")
        print("-" * 58)

        results = {}
        for mode in (0, 1, 2, 3, 4):
            base = len(frames)
            await client.write_gatt_char(WRITE, proto.set_maf(mode), response=False)
            await asyncio.sleep(0.3)
            ack = None
            for _t, f in frames[base:]:
                if f.ftype == 2 and f.cid == 44:
                    ack = "OK" if (f.payload and f.payload[0] == 0) else f"E:{f.payload}"
                    break
            v06 = await get_maf()
            await asyncio.sleep(1.1)
            v20 = await get_maf()
            await asyncio.sleep(1.0)
            v30 = await get_maf()
            ok = v30 == mode
            results[mode] = ok
            print(f"{mode:<6} {str(ack):<6} {str(v06):<8} {str(v20):<8} {str(v30):<8} "
                  f"{'✓ 生效' if ok else '✗ 未生效'}")

        # 恢复原值
        if original is not None:
            _, _ = None, None
            await client.write_gatt_char(WRITE, proto.set_maf(original), response=False)
            await asyncio.sleep(2.2)
            back = await get_maf()
            print(f"\n恢复 MAF={original} -> 读回 {back} "
                  f"{'OK' if back == original else 'FAIL'}")

        await client.stop_notify(NOTIFY)

    print("\n=== 结论 ===")
    supported = [m for m, ok in results.items() if ok]
    unsupported = [m for m, ok in results.items() if not ok]
    print(f"实机支持: {supported}")
    print(f"实机不支持: {unsupported}")


if __name__ == "__main__":
    asyncio.run(main())
