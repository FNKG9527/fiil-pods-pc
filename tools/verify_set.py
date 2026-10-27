"""SET 命令实机回归：写入 → 读回 → 恢复原值

只测两个可恢复的语义化控制（MAF / 影院），全程记录，结束时恢复原始状态。
输出的日志会作为 PROTOCOL_PC.md 的「已确认」证据。

用法：python verify_set.py
"""

from __future__ import annotations

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import asyncio
import sys
from datetime import datetime
from pathlib import Path

from bleak import BleakClient

from fiil_protocol import (
    CHAR_NOTIFY_UUID,
    CHAR_WRITE_UUID,
    FiilProtocol,
    Frame,
    MafMode,
    parse_bool,
    parse_uint,
)

OUT = Path(__file__).resolve().parent.parent / "out"
ORG = "B0:F1:A3:00:00:01"
LOG: list[str] = []


def log(s: str) -> None:
    print(s)
    LOG.append(s)


async def main() -> int:
    proto = FiilProtocol()
    rx: list[Frame] = []

    def on_notify(_c, data: bytearray) -> None:
        rx.extend(proto.feed(bytes(data)))

    async with BleakClient(ORG, timeout=20.0) as client:
        await client.start_notify(CHAR_NOTIFY_UUID, on_notify)
        await asyncio.sleep(0.3)

        async def send(name: str, frame: bytes, wait: float = 0.6) -> Frame | None:
            n0 = len(rx)
            await client.write_gatt_char(CHAR_WRITE_UUID, frame, response=False)
            log(f"[TX] {name:<22} {frame.hex(' ').upper()}")
            await asyncio.sleep(wait)
            new = rx[n0:]
            for f in new:
                log(f"     [RX] {f.hex()}   ({f.describe()})")
            return new[-1] if new else None

        log("=" * 78)
        log(f"# SET 回归 {datetime.now().isoformat(timespec='seconds')}  设备 {ORG}")
        log("=" * 78)

        # --- 握手 ---
        ack = await send("setUnlock", proto.request_unlock())
        if not ack or ack.cmd != 0xFF:
            log("[!] 握手失败，中止")
            return 1

        # --- 读原值 ---
        bat0 = await send("GET_BATTERY", proto.request_battery())
        log(f"     电量解析：{FiilProtocol.battery(bat0).display() if bat0 else '—'}")

        maf0 = await send("GET_MAF (原值)", proto.request_maf())
        v0 = parse_uint(maf0.payload) if maf0 else None
        log(f"     原 MAF = {v0} ({MafMode(v0).label_cn if v0 is not None and v0 in [m.value for m in MafMode] else '?'})")

        cin0 = await send("GET_SPATIAL (原值)", proto.request_cinema())
        c0 = parse_bool(cin0.payload) if cin0 else None
        log(f"     原影院 = {c0}")

        # --- MAF：切到「降噪」，读回，再切回原值 ---
        log("")
        log("--- 测 MAF ---")
        target = MafMode.NOISE_CANCEL if v0 != int(MafMode.NOISE_CANCEL) else MafMode.OFF
        await send(f"SET_MAF={int(target)}({target.label_cn})", proto.set_maf(target))
        back = await send("GET_MAF (读回)", proto.request_maf())
        vb = parse_uint(back.payload) if back else None
        log(f"     >>> MAF 读回 = {vb}  期望 {int(target)}  → {'一致 ✓' if vb == int(target) else '不一致 ✗'}")

        if v0 is not None:
            await send(f"恢复 MAF={v0}", proto.set_maf(MafMode(v0)))
            r = await send("GET_MAF (恢复确认)", proto.request_maf())
            vr = parse_uint(r.payload) if r else None
            log(f"     >>> 恢复确认 = {vr}  期望 {v0}  → {'✓' if vr == v0 else '✗'}")

        # --- 影院：翻转，读回，恢复 ---
        log("")
        log("--- 测影院（空间音频）---")
        tgt = not bool(c0)
        await send(f"SET_SPATIAL={int(tgt)}", proto.set_cinema(tgt))
        back = await send("GET_SPATIAL (读回)", proto.request_cinema())
        cb = parse_bool(back.payload) if back else None
        log(f"     >>> 影院读回 = {cb}  期望 {tgt}  → {'一致 ✓' if cb == tgt else '不一致 ✗'}")

        if c0 is not None:
            await send(f"恢复 SPATIAL={int(c0)}", proto.set_cinema(bool(c0)))
            r = await send("GET_SPATIAL (恢复确认)", proto.request_cinema())
            cr = parse_bool(r.payload) if r else None
            log(f"     >>> 恢复确认 = {cr}  期望 {c0}  → {'✓' if cr == c0 else '✗'}")

        await client.stop_notify(CHAR_NOTIFY_UUID)

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"set_verify_{datetime.now():%Y%m%d_%H%M%S}.txt"
    p.write_text("\n".join(LOG) + "\n", encoding="utf-8")
    print(f"\n[+] 已保存 {p}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
