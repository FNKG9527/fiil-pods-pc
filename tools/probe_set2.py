"""FIIL GATT SET 探测 v2 —— 用正确结构（cid=命令族，cmdId=0）
并修正帧组装：writeMate 里 `if (commandId != 0)` 才写 (len+2)+cmdId，
cmdId==0 时只写 payloadLen 然后 payload —— 即长度字段不带 +2。

顺序：读原值 → SET（新结构）→ 读回确认 → 恢复原值。
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
    """严格照 writeMate 组装。cmd=0 时长度字段 = payloadLen（不含 +2）。"""
    global SEQ
    s = SEQ & 0x0F
    SEQ = (SEQ + 1) & 0x0F
    plen = len(payload)
    head = [s, cid & 0xFF, 0x01, 0x00]
    if cmd != 0:
        head.append((plen + 2) & 0xFF)
        head.append(cmd & 0xFF)
    # writeMate 在 if 分支之后**无条件**再写一次 payloadLen
    head.append(plen & 0xFF)
    return bytes(head) + payload


class Probe:
    def __init__(self, client: BleakClient) -> None:
        self.client = client
        self.events: list[bytes] = []

    def on_notify(self, _c, data: bytearray) -> None:
        self.events.append(bytes(data))
        print(f"    << {hexs(data)}")

    async def tx(self, name: str, frame: bytes, wait: float = 0.8) -> bytes | None:
        before = len(self.events)
        print(f"[TX] {name:<26} {hexs(frame)}")
        await self.client.write_gatt_char(CHAR_WRITE, frame, response=False)
        await asyncio.sleep(wait)
        new = self.events[before:]
        return new[-1] if new else None


def val_of(pkt: bytes | None) -> int | None:
    """取回包里的状态值：帧 [0..4]=seq,cid,type,0,lenField; 有 cmd 时 [5]=cmd,[6]=len,[7]=value"""
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

    async with BleakClient(dev.address, timeout=15.0) as client:
        p = Probe(client)
        await client.start_notify(CHAR_NOTIFY, p.on_notify)
        await p.tx("setUnlock", build(39, 0xFF, bytes([0x02, 0x34])))

        rows = []

        # ---------- MAF (cid=44, cmd=0) ----------
        orig = await p.tx("GET_MAF", build(39, 12))
        om = val_of(orig)
        rows.append(("GET_MAF 原值", hexs(orig) if orig else "-", f"MAF={om}"))
        if om is not None:
            tgt = 2 if om != 2 else 4
            ack = await p.tx(f"SET_MAF={tgt} (cid44)", build(44, 0, bytes([tgt])))
            rows.append((f"SET_MAF {tgt}", hexs(ack) if ack else "(无回包)", "cid=44,cmd=0"))
            back = await p.tx("GET_MAF 读回", build(39, 12))
            rows.append(("GET_MAF 读回", hexs(back) if back else "-", f"MAF={val_of(back)}"))
            await p.tx(f"SET_MAF 恢复{om}", build(44, 0, bytes([om])))
            r = await p.tx("GET_MAF 恢复确认", build(39, 12))
            rows.append(("恢复确认", hexs(r) if r else "-", f"MAF={val_of(r)}"))

        # ---------- 低延迟 (cid=37, cmd=0) ----------
        orig = await p.tx("GET_LL", build(39, 8))
        ol = val_of(orig)
        rows.append(("GET_LL 原值", hexs(orig) if orig else "-", f"LL={ol}"))
        if ol is not None:
            tgt = 1 if ol == 0 else 0
            ack = await p.tx(f"SET_LL={tgt} (cid37)", build(37, 0, bytes([tgt])))
            rows.append((f"SET_LL {tgt}", hexs(ack) if ack else "(无回包)", "cid=37,cmd=0"))
            back = await p.tx("GET_LL 读回", build(39, 8))
            rows.append(("GET_LL 读回", hexs(back) if back else "-", f"LL={val_of(back)}"))
            await p.tx(f"SET_LL 恢复{ol}", build(37, 0, bytes([ol])))
            r = await p.tx("GET_LL 恢复确认", build(39, 8))
            rows.append(("恢复确认", hexs(r) if r else "-", f"LL={val_of(r)}"))

        # ---------- 多点 (cid=1, cmd=1) ----------
        orig = await p.tx("GET_MP", build(39, 128))
        omp = val_of(orig)
        rows.append(("GET_MP 原值", hexs(orig) if orig else "-", f"MP={omp}"))
        if omp is not None:
            tgt = 0 if omp == 1 else 1
            ack = await p.tx(f"SET_MP={tgt} (cid1/cmd1)", build(1, 1, bytes([tgt])))
            rows.append((f"SET_MP {tgt}", hexs(ack) if ack else "(无回包)", "cid=1,cmd=1"))
            back = await p.tx("GET_MP 读回", build(39, 128))
            rows.append(("GET_MP 读回", hexs(back) if back else "-", f"MP={val_of(back)}"))
            await p.tx(f"SET_MP 恢复{omp}", build(1, 1, bytes([omp])))
            r = await p.tx("GET_MP 恢复确认", build(39, 128))
            rows.append(("恢复确认", hexs(r) if r else "-", f"MP={val_of(r)}"))

        # ---------- 影院/空间音频 (cid=50, cmd=0) ----------
        orig = await p.tx("GET_SPATIAL", build(39, 24))
        os_ = val_of(orig)
        rows.append(("GET_SPATIAL 原值", hexs(orig) if orig else "-", f"SP={os_}"))
        if os_ is not None:
            tgt = 0 if os_ == 1 else 1
            ack = await p.tx(f"SET_SPATIAL={tgt} (cid50)", build(50, 0, bytes([tgt])))
            rows.append((f"SET_SPATIAL {tgt}", hexs(ack) if ack else "(无回包)", "cid=50,cmd=0"))
            back = await p.tx("GET_SPATIAL 读回", build(39, 24))
            rows.append(("读回", hexs(back) if back else "-", f"SP={val_of(back)}"))
            await p.tx(f"SET_SPATIAL 恢复{os_}", build(50, 0, bytes([os_])))
            r = await p.tx("恢复确认", build(39, 24))
            rows.append(("恢复确认", hexs(r) if r else "-", f"SP={val_of(r)}"))

        print()
        print("=" * 96)
        print(f"{'步骤':<24} {'回包':<44} 解读")
        print("=" * 96)
        for n, h, note in rows:
            print(f"{n:<24} {h:<44} {note}")

        os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"), exist_ok=True)
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out",
                            f"set2_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# FIIL GATT SET 探测 v2  {datetime.now().isoformat(timespec='seconds')}\n")
            fh.write(f"# 设备 {dev.address}\n\n")
            for n, h, note in rows:
                fh.write(f"{n:<24}\t{h:<44}\t{note}\n")
        print(f"\n[+] 已保存 {path}")

        try:
            await client.stop_notify(CHAR_NOTIFY)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    asyncio.run(main())
