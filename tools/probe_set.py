"""FIIL GATT SET 命令探测 —— 逐项验证写入是否生效，并在结束时恢复原值。

顺序：先读原值 → 改 → 读回确认 → 恢复原值。
只动可逆项（MAF 模式、低延迟、多点），不碰 EQ 曲线与恢复出厂。
"""

from __future__ import annotations

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import asyncio
import os
import sys
import time
from datetime import datetime

from bleak import BleakClient, BleakScanner

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

CHAR_NOTIFY = "0000ff18-0000-1000-8000-00805f9b34fb"
CHAR_WRITE = "0000ff17-0000-1000-8000-00805f9b34fb"
FIIL_OUI = ("B0:F1:A3", "FF:F1:A3")

SEQ = 0


def hexs(b: bytes) -> str:
    return b.hex(" ").upper()


def build(cid: int, cmd: int, payload: bytes = b"") -> bytes:
    global SEQ
    s = SEQ & 0x0F
    SEQ = (SEQ + 1) & 0x0F
    plen = len(payload)
    return bytes([s, cid, 0x01, 0x00, plen + 2, cmd, plen]) + payload


class Probe:
    def __init__(self, client: BleakClient) -> None:
        self.client = client
        self.events: list[bytes] = []
        self.t0 = time.time()

    def on_notify(self, _c, data: bytearray) -> None:
        self.events.append(bytes(data))
        print(f"    << {hexs(bytes(data))}")

    async def tx(self, name: str, frame: bytes, wait: float = 0.7) -> bytes | None:
        before = len(self.events)
        print(f"[TX] {name:<22} {hexs(frame)}")
        await self.client.write_gatt_char(CHAR_WRITE, frame, response=False)
        await asyncio.sleep(wait)
        new = self.events[before:]
        return new[-1] if new else None


async def main() -> None:
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    dev = next((d for d, _ in found.values() if d.address.upper()[:8] in FIIL_OUI), None)
    if dev is None:
        print("[!] 未找到设备")
        return
    print(f"[+] {dev.address}")

    async with BleakClient(dev.address, timeout=15.0) as client:
        p = Probe(client)
        await client.start_notify(CHAR_NOTIFY, p.on_notify)
        await p.tx("setUnlock", build(39, 0xFF, bytes([0x02, 0x34])))

        results = []

        # ---- MAF 模式：读 → 改为 1(降噪) → 读回 → 恢复 ----
        orig = await p.tx("GET_MAF", build(39, 12))
        orig_maf = orig[6] if orig and len(orig) >= 7 else None
        results.append(("GET_MAF(原值)", hexs(orig) if orig else "-", f"MAF={orig_maf}"))

        target = 1 if (orig_maf or 0) != 1 else 2
        ack = await p.tx(f"SET_MAF={target}", build(39, 44, bytes([target])))
        results.append((f"SET_MAF({target})", f"payload {target:02X}", hexs(ack) if ack else "(无回包)"))
        back = await p.tx("GET_MAF(读回)", build(39, 12))
        results.append(("GET_MAF(读回)", hexs(back) if back else "-",
                        f"MAF={back[6] if back and len(back) >= 7 else '?'}"))

        if orig_maf is not None:
            await p.tx(f"SET_MAF(恢复{orig_maf})", build(39, 44, bytes([orig_maf])))
            r = await p.tx("GET_MAF(恢复确认)", build(39, 12))
            results.append(("恢复确认", hexs(r) if r else "-",
                            f"MAF={r[6] if r and len(r) >= 7 else '?'}"))

        # ---- 低延迟：读 → 改 → 读回 → 恢复 ----
        orig = await p.tx("GET_LOW_LATENCY", build(39, 8))
        orig_ll = orig[6] if orig and len(orig) >= 7 else None
        results.append(("GET_LL(原值)", hexs(orig) if orig else "-", f"LL={orig_ll}"))

        target = 1 if (orig_ll or 0) != 1 else 0
        ack = await p.tx(f"SET_LOW_LATENCY={target}", build(39, 37, bytes([target])))
        results.append((f"SET_LL({target})", f"payload {target:02X}", hexs(ack) if ack else "(无回包)"))
        back = await p.tx("GET_LL(读回)", build(39, 8))
        results.append(("GET_LL(读回)", hexs(back) if back else "-",
                        f"LL={back[6] if back and len(back) >= 7 else '?'}"))

        if orig_ll is not None:
            await p.tx(f"SET_LL(恢复{orig_ll})", build(39, 37, bytes([orig_ll])))
            r = await p.tx("GET_LL(恢复确认)", build(39, 8))
            results.append(("恢复确认", hexs(r) if r else "-",
                            f"LL={r[6] if r and len(r) >= 7 else '?'}"))

        # ---- 多点：读 → 反转 → 读回 → 恢复 ----
        orig = await p.tx("GET_MULTIPOINT", build(39, 128))
        orig_mp = orig[6] if orig and len(orig) >= 7 else None
        results.append(("GET_MP(原值)", hexs(orig) if orig else "-", f"MP={orig_mp}"))

        target = 0 if (orig_mp or 0) == 1 else 1
        ack = await p.tx(f"SET_MULTIPOINT={target}", build(39, 1, bytes([target])))
        results.append((f"SET_MP({target})", f"payload {target:02X}", hexs(ack) if ack else "(无回包)"))
        back = await p.tx("GET_MP(读回)", build(39, 128))
        results.append(("GET_MP(读回)", hexs(back) if back else "-",
                        f"MP={back[6] if back and len(back) >= 7 else '?'}"))

        if orig_mp is not None:
            await p.tx(f"SET_MP(恢复{orig_mp})", build(39, 1, bytes([orig_mp])))
            r = await p.tx("GET_MP(恢复确认)", build(39, 128))
            results.append(("恢复确认", hexs(r) if r else "-",
                            f"MP={r[6] if r and len(r) >= 7 else '?'}"))

        print()
        print("=" * 100)
        print(f"{'步骤':<24} {'发送/回包':<40} 解读")
        print("=" * 100)
        for name, rx, note in results:
            print(f"{name:<24} {rx:<40} {note}")

        os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"), exist_ok=True)
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "out",
            f"set_{time.strftime('%Y%m%d_%H%M%S')}.txt",
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# FIIL GATT SET 探测  {datetime.now().isoformat(timespec='seconds')}\n")
            fh.write(f"# 设备 {dev.address}\n\n")
            for name, rx, note in results:
                fh.write(f"{name:<24}\t{rx:<40}\t{note}\n")
        print(f"\n[+] 已保存 {path}")

        try:
            await client.stop_notify(CHAR_NOTIFY)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    asyncio.run(main())
