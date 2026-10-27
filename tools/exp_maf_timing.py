"""MAF 写入时序专项实验

目的：判定「SET 之后立刻 GET 读到旧值」到底是
  (A) SET 未被接受
  (B) 耳机异步应用，GET 早于生效
  (C) 状态推送（cid=40）是唯一可靠来源

方法：SET 后分别等 0.2/0.8/1.5/3.0 秒再 GET，并记录期间所有 NOTIFY 原始帧。
"""

from __future__ import annotations

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import asyncio
from datetime import datetime
from pathlib import Path

from bleak import BleakClient

from fiil_protocol import (
    CHAR_NOTIFY_UUID,
    CHAR_WRITE_UUID,
    FiilProtocol,
    Frame,
    MafMode,
    parse_uint,
)

ORG = "B0:F1:A3:00:00:01"
OUT = Path(__file__).resolve().parent.parent / "out"
LOG: list[str] = []


def log(s: str) -> None:
    print(s)
    LOG.append(s)


async def main() -> None:
    proto = FiilProtocol()
    rx: list[Frame] = []
    raw_notify: list[tuple[float, bytes]] = []
    t0 = 0.0

    def on_notify(_c, data: bytearray) -> None:
        raw_notify.append((asyncio.get_event_loop().time() - t0, bytes(data)))
        rx.extend(proto.feed(bytes(data)))

    async with BleakClient(ORG, timeout=20.0) as client:
        await client.start_notify(CHAR_NOTIFY_UUID, on_notify)
        await asyncio.sleep(0.3)
        t0 = asyncio.get_event_loop().time()

        async def send(name: str, frame: bytes, wait: float = 0.6) -> Frame | None:
            n0 = len(rx)
            await client.write_gatt_char(CHAR_WRITE_UUID, frame, response=False)
            log(f"[TX] {name:<20} {frame.hex(' ').upper()}")
            await asyncio.sleep(wait)
            new = rx[n0:]
            for f in new:
                log(f"     [RX] {f.hex()}")
            return new[-1] if new else None

        log("=" * 70)
        log(f"# MAF 时序实验 {datetime.now().isoformat(timespec='seconds')}")
        log("=" * 70)

        await send("setUnlock", proto.request_unlock())

        cur = await send("GET_MAF(初始)", proto.request_maf())
        v = parse_uint(cur.payload) if cur else None
        log(f"     初始 MAF = {v}")

        target = int(MafMode.OFF) if v != int(MafMode.OFF) else int(MafMode.NOISE_CANCEL)
        log("")
        log(f"--- 写入 MAF={target}，然后分时读回 ---")
        n0 = len(rx)
        set_frame = proto.set_maf(MafMode(target))   # 只组一次帧，避免 seq 多消耗
        await client.write_gatt_char(CHAR_WRITE_UUID, set_frame, response=False)
        log(f"[TX] SET_MAF={target}  {set_frame.hex(' ').upper()}")

        for delay in (0.2, 0.8, 1.5, 3.0):
            await asyncio.sleep(delay - (0 if delay == 0.2 else 0))
            r = await send(f"GET_MAF(+{delay}s)", proto.request_maf(), wait=0.5)
            got = parse_uint(r.payload) if r else None
            log(f"     >>> +{delay}s 读回 = {got}  期望 {target}  {'✓' if got == target else '✗'}")

        log("")
        log("--- 期间全部原始 notify ---")
        for dt, pkt in raw_notify[n0:]:
            log(f"     +{dt:6.3f}s  {pkt.hex(' ').upper()}")

        # 恢复
        if v is not None:
            await send(f"恢复 MAF={v}", proto.set_maf(MafMode(v)))
            await asyncio.sleep(1.5)
            r = await send("GET_MAF(恢复)", proto.request_maf())
            log(f"     >>> 恢复读回 = {parse_uint(r.payload) if r else None}  期望 {v}")

        await client.stop_notify(CHAR_NOTIFY_UUID)

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"maf_timing_{datetime.now():%Y%m%d_%H%M%S}.txt"
    p.write_text("\n".join(LOG) + "\n", encoding="utf-8")
    print(f"\n[+] 已保存 {p}")


if __name__ == "__main__":
    asyncio.run(main())
