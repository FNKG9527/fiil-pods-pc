"""FIIL GATT SET 探测 v3 —— 确认多点 / 空间音频，并验证 NOTIFY 通知通道

v2 已证实：
  SET 帧 = [seq][cid=命令族][01][00][payloadLen][payload]   ← 无 cmdId
  应答   = [seq][cid][02][00][01][00]                        ← 1 字节成功码
  通知   = [seq][0x28=40][03][00][len][cmd][len][value]      ← cid=40 是耳机主动推送
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


def hexs(b) -> str:
    return bytes(b).hex(" ").upper()


def build(cid: int, cmd: int, payload: bytes = b"") -> bytes:
    global SEQ
    s = SEQ & 0x0F
    SEQ = (SEQ + 1) & 0x0F
    plen = len(payload)
    head = [s, cid & 0xFF, 0x01, 0x00]
    if cmd != 0:
        head.append((plen + 2) & 0xFF)
        head.append(cmd & 0xFF)
    head.append(plen & 0xFF)
    return bytes(head) + payload


class Probe:
    def __init__(self, client: BleakClient) -> None:
        self.client = client
        self.events: list[bytes] = []
        self.notifies: list[bytes] = []

    def on_notify(self, _c, data: bytearray) -> None:
        pkt = bytes(data)
        self.events.append(pkt)
        tag = "NOTIFY" if len(pkt) > 2 and pkt[2] == 0x03 else "RESP  "
        print(f"    << {tag} {hexs(pkt)}")

    async def tx(self, name: str, frame: bytes, wait: float = 0.8) -> bytes | None:
        before = len(self.events)
        print(f"[TX] {name:<24} {hexs(frame)}")
        await self.client.write_gatt_char(CHAR_WRITE, frame, response=False)
        await asyncio.sleep(wait)
        for pkt in self.events[before:]:
            if len(pkt) > 2 and pkt[2] == 0x03:
                self.notifies.append(pkt)
        new = [p for p in self.events[before:] if len(p) > 2 and pkt_ok(p)]
        return new[-1] if new else None


def pkt_ok(p: bytes) -> bool:
    return len(p) >= 5


def val_of(pkt: bytes | None) -> int | None:
    if not pkt:
        return None
    lf = pkt[4]
    if lf <= 1:
        return None
    if len(pkt) >= 8 and pkt[6] >= 1:
        return pkt[7]
    if len(pkt) >= 7:
        return pkt[6]
    return None


async def main() -> None:
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    dev = next((d for d, _ in found.values() if d.address.upper()[:8] in FIIL_OUI), None)
    if dev is None:
        print("[!] 未找到设备")
        return
    print(f"[+] {dev.address}")

    rows: list[tuple[str, str, str]] = []

    async with BleakClient(dev.address, timeout=20.0) as client:
        p = Probe(client)
        await client.start_notify(CHAR_NOTIFY, p.on_notify)
        await p.tx("setUnlock", build(39, 0xFF, bytes([0x02, 0x34])))

        # ---- 空间音频 / 影院模式：cid=50, cmd=0 ----
        orig = await p.tx("GET_SPATIAL", build(39, 24))
        os_ = val_of(orig)
        rows.append(("GET_SPATIAL 原值", hexs(orig) if orig else "-", f"SP={os_}"))
        if os_ is not None:
            tgt = 0 if os_ == 1 else 1
            ack = await p.tx(f"SET_SPATIAL={tgt}", build(50, 0, bytes([tgt])), wait=1.2)
            rows.append((f"SET_SPATIAL {tgt}", hexs(ack) if ack else "(无回包)", "cid=50,cmd=0"))
            back = await p.tx("GET_SPATIAL 读回", build(39, 24))
            rows.append(("读回", hexs(back) if back else "-", f"SP={val_of(back)}"))
            await p.tx(f"SET_SPATIAL 恢复{os_}", build(50, 0, bytes([os_])))
            r = await p.tx("恢复确认", build(39, 24))
            rows.append(("恢复确认", hexs(r) if r else "-", f"SP={val_of(r)}"))
        else:
            rows.append(("SET_SPATIAL", "(跳过)", "耳机未返回空间音频状态 → 该机型不支持"))

        # ---- 多点：cid=1, cmd=1 ----
        orig = await p.tx("GET_MP", build(39, 128))
        omp = val_of(orig)
        rows.append(("GET_MP 原值", hexs(orig) if orig else "-", f"MP={omp}"))
        if omp is not None:
            tgt = 0 if omp == 1 else 1
            ack = await p.tx(f"SET_MP={tgt}", build(1, 1, bytes([tgt])), wait=1.2)
            rows.append((f"SET_MP {tgt}", hexs(ack) if ack else "(无回包)", "cid=1,cmd=1"))
            back = await p.tx("GET_MP 读回", build(39, 128))
            rows.append(("读回", hexs(back) if back else "-", f"MP={val_of(back)}"))
            await p.tx(f"SET_MP 恢复{omp}", build(1, 1, bytes([omp])))
            r = await p.tx("恢复确认", build(39, 128))
            rows.append(("恢复确认", hexs(r) if r else "-", f"MP={val_of(r)}"))

        # ---- EQ：cid=32, cmd=0, 12 字节 ----
        orig = await p.tx("GET_EQ", build(39, 4))
        rows.append(("GET_EQ 原值", hexs(orig) if orig else "-", "12 段曲线"))

        print()
        print("=" * 104)
        print(f"{'步骤':<24} {'回包':<46} 解读")
        print("=" * 104)
        for n, h, note in rows:
            print(f"{n:<24} {h:<46} {note}")

        print()
        print(f"# 耳机主动推送（NOTIFY, cid=40）共 {len(p.notifies)} 条：")
        for pkt in p.notifies:
            print(f"  {hexs(pkt)}")

        os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"), exist_ok=True)
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out",
                            f"set3_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# FIIL GATT SET 探测 v3  {datetime.now().isoformat(timespec='seconds')}\n")
            fh.write(f"# 设备 {dev.address}\n\n")
            for n, h, note in rows:
                fh.write(f"{n:<24}\t{h:<46}\t{note}\n")
            fh.write("\n# NOTIFY (cid=40)\n")
            for pkt in p.notifies:
                fh.write(hexs(pkt) + "\n")
        print(f"\n[+] 已保存 {path}")

        try:
            await client.stop_notify(CHAR_NOTIFY)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    asyncio.run(main())
