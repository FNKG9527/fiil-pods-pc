"""验证 低延迟 / 蓝牙畅连 / 语音提示音量 三条命令的真实行为。"""

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

rx_buf = bytearray()
frames = []

def on_notify(_c, data: bytearray):
    rx_buf.extend(data)
    got, rest = drain(bytes(rx_buf))
    rx_buf.clear(); rx_buf.extend(rest)
    for fr in got:
        frames.append(fr)
        print("  RX <<", fr.hex())

async def send(cli, label, data, wait=2.2):
    print(f"[TX] {label:<22} {data.hex(' ')}")
    frames.clear()
    await cli.write_gatt_char(W, data, response=False)
    await asyncio.sleep(wait)
    if not frames:
        print("     <none>")
    return list(frames)

async def main():
    async with BleakClient(ADDR, timeout=20.0) as cli:
        await cli.start_notify(N, on_notify)
        await asyncio.sleep(0.5)
        p = FiilProtocol()
        await send(cli, "setUnlock", p.request_unlock())
        await send(cli, "GET_TONE(130)", p._get(Query.TONE))
        await send(cli, "GET_LL(8)", p.request_low_latency())
        await send(cli, "GET_CHANGLAN(128)", p.request_multipoint())

        print("\n--- 低延迟 SET (cid=37) ---")
        for lv in (1, 2, 0):
            await send(cli, f"SET_LL={lv}", p.set_low_latency(lv), 1.2)
            await send(cli, "  -> GET_LL", p.request_low_latency(), 1.2)

        print("\n--- 语音提示音量 SET (cid=1 cmd=3) ---")
        for v in (2, 1, 0):
            await send(cli, f"SET_TONE={v}", build_frame(int(Cid.SET_PARAM), 3, bytes([v]), p._next_seq()), 1.5)
            await send(cli, "  -> GET_TONE", p._get(Query.TONE), 1.5)
        await send(cli, "SET_TONE=2(restore)", build_frame(int(Cid.SET_PARAM), 3, bytes([2]), p._next_seq()), 1.5)

        print("\n--- 畅连 SET (cid=1 cmd=1) 放最后，可能断链 ---")
        await send(cli, "SET_MP=0", p.set_multipoint(False), 3.0)
        try:
            await send(cli, "  -> GET_MP", p.request_multipoint(), 2.0)
        except Exception as e:
            print("     (断链预期):", type(e).__name__, e)

asyncio.run(main())
