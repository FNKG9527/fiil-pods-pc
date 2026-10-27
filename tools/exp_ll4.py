"""低延迟 0/1/2/3 四档全测 + 畅连回开。"""

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import asyncio

from bleak import BleakClient

from fiil_protocol import Cid, FiilProtocol, Query, build_frame, drain

ADDR = "B0:F1:A3:00:00:01"
W = "0000ff17-0000-1000-8000-00805f9b34fb"
N = "0000ff18-0000-1000-8000-00805f9b34fb"
rx = bytearray(); frames = []

def on_notify(_c, data):
    rx.extend(data)
    got, rest = drain(bytes(rx)); rx.clear(); rx.extend(rest)
    for fr in got:
        frames.append(fr); print("  RX <<", fr.hex())

async def send(cli, label, data, wait=1.5):
    print(f"[TX] {label:<20} {data.hex(' ')}")
    frames.clear()
    await cli.write_gatt_char(W, data, response=False)
    await asyncio.sleep(wait)
    if not frames: print("     <none>")
    return list(frames)

async def main():
    async with BleakClient(ADDR, timeout=20.0) as cli:
        await cli.start_notify(N, on_notify); await asyncio.sleep(0.5)
        p = FiilProtocol()
        await send(cli, "setUnlock", p.request_unlock())
        print("当前:", [f.payload.hex() for f in await send(cli, "GET_LL", p.request_low_latency())])
        print("\n--- 低延迟四档 0/1/2/3 ---")
        for lv in (1, 2, 3, 0):
            ack = await send(cli, f"SET_LL={lv}", p.set_low_latency(lv), 1.8)
            rb = await send(cli, "  -> GET_LL", p.request_low_latency(), 1.2)
            v = rb[0].payload[0] if rb and rb[0].payload else None
            print(f"     >> 写入 {lv} 读回 {v}  {'OK' if v==lv else 'MISMATCH'}")
        print("\n--- 畅连回开 (1) ---")
        await send(cli, "SET_MP=1", p.set_multipoint(True), 3.0)
        try:
            rb = await send(cli, "  -> GET_MP", p.request_multipoint(), 2.0)
            print("     >> 畅连 =", rb[0].payload.hex() if rb else "?")
        except Exception as e:
            print("     (断链):", type(e).__name__)

asyncio.run(main())
