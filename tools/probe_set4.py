"""FIIL GATT 探测 v4 —— 断线自动重连 + 补测多点/EQ/按键

v3 已证实可用（全部读回确认）：
  MAF        cid=44   payload{0..4}
  低延迟      cid=37   payload{0..3}
  空间音频    cid=50   payload{0=关,1=开}
  多点       cid=1 cmd=1 payload{0/1}     ← 设置后 BLE 会掉线，需重连确认
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
LOG: list[str] = []


def hexs(b) -> str:
    return bytes(b).hex(" ").upper()


def log(s: str) -> None:
    print(s)
    LOG.append(s)


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


class Session:
    def __init__(self, addr: str) -> None:
        self.addr = addr
        self.client: BleakClient | None = None
        self.events: list[bytes] = []
        self.notifies: list[bytes] = []

    def _on_notify(self, _c, data: bytearray) -> None:
        pkt = bytes(data)
        self.events.append(pkt)
        tag = "NOTIFY" if len(pkt) > 2 and pkt[2] == 0x03 else "RESP  "
        log(f"    << {tag} {hexs(pkt)}")
        if len(pkt) > 2 and pkt[2] == 0x03:
            self.notifies.append(pkt)

    async def open(self) -> bool:
        try:
            self.client = BleakClient(self.addr, timeout=20.0)
            await self.client.connect()
            await self.client.start_notify(CHAR_NOTIFY, self._on_notify)
            log(f"[+] 已连接 {self.addr}")
            return True
        except Exception as e:  # noqa: BLE001
            log(f"[!] 连接失败: {e}")
            return False

    async def close(self) -> None:
        try:
            if self.client and self.client.is_connected:
                await self.client.stop_notify(CHAR_NOTIFY)
                await self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.client = None

    def alive(self) -> bool:
        return bool(self.client and self.client.is_connected)

    async def tx(self, name: str, frame: bytes, wait: float = 0.9) -> bytes | None:
        if not self.alive():
            log(f"[!] 断开，重连后再发 {name}")
            await self.close()
            await asyncio.sleep(1.5)
            if not await self.open():
                return None
            await self.tx("setUnlock(retry)", build(39, 0xFF, bytes([0x02, 0x34])))
        before = len(self.events)
        log(f"[TX] {name:<24} {hexs(frame)}")
        try:
            await self.client.write_gatt_char(CHAR_WRITE, frame, response=False)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            log(f"[!] 写失败 {e}")
            return None
        await asyncio.sleep(wait)
        new = [p for p in self.events[before:] if len(p) >= 5]
        return new[-1] if new else None


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
        log("[!] 未找到设备")
        return
    log(f"[+] 目标 {dev.address}")

    s = Session(dev.address)
    if not await s.open():
        return
    await s.tx("setUnlock", build(39, 0xFF, bytes([0x02, 0x34])))

    rows: list[tuple[str, str, str]] = []

    # ---- 1. 多点：先读，再写，重连后读回 ----
    orig = await s.tx("GET_MP", build(39, 128))
    omp = val_of(orig)
    rows.append(("GET_MP 原值", hexs(orig) if orig else "-", f"MP={omp}"))
    if omp is not None:
        tgt = 0 if omp == 1 else 1
        ack = await s.tx(f"SET_MP={tgt}", build(1, 1, bytes([tgt])), wait=2.5)
        rows.append((f"SET_MP {tgt}", hexs(ack) if ack else "(无回包)", "cid=1,cmd=1"))
        log("[*] 多点切换后耳机通常会断链，等待重连…")
        await asyncio.sleep(3.0)
        if not s.alive():
            await s.open()
            await s.tx("setUnlock", build(39, 0xFF, bytes([0x02, 0x34])))
        back = await s.tx("GET_MP 读回", build(39, 128))
        rows.append(("GET_MP 读回", hexs(back) if back else "-", f"MP={val_of(back)}"))
        if val_of(back) != omp:
            await s.tx(f"SET_MP 恢复{omp}", build(1, 1, bytes([omp])), wait=2.5)
            await asyncio.sleep(3.0)
            if not s.alive():
                await s.open()
                await s.tx("setUnlock", build(39, 0xFF, bytes([0x02, 0x34])))
            r = await s.tx("恢复确认", build(39, 128))
            rows.append(("恢复确认", hexs(r) if r else "-", f"MP={val_of(r)}"))

    # ---- 2. EQ 写入：cid=32 ----
    origEq = await s.tx("GET_EQ", build(39, 4))
    rows.append(("GET_EQ 原值", hexs(origEq) if origEq else "-", "12 段"))
    if origEq and len(origEq) >= 19:
        # 回包 [0..4] seq,cid,type,0,lf  [5]=cmd(4) [6]=len(12) [7..18]=12 bytes
        cur = bytes(origEq[7:19])
        rows.append(("原曲线 12 字节", hexs(cur), ""))
        # 造一条轻微改动：把第 1 段 +2，其余保持
        new = bytearray(cur)
        new[0] = max(0, min(0x0E, new[0] + 2))
        ack = await s.tx("SET_EQ", build(32, 0, bytes(new)), wait=1.5)
        rows.append(("SET_EQ 写入", hexs(ack) if ack else "(无回包)", "cid=32,cmd=0,payload=12B"))
        back = await s.tx("GET_EQ 读回", build(39, 4))
        rows.append(("GET_EQ 读回", hexs(back) if back else "-", ""))
        if back and len(back) >= 19:
            got = bytes(back[7:19])
            ok = got == bytes(new)
            rows.append(("写入一致性", "一致 ✓" if ok else f"不一致 ✗ 读回 {hexs(got)}", ""))
        # 恢复
        await s.tx("SET_EQ 恢复", build(32, 0, cur), wait=1.5)
        r = await s.tx("GET_EQ 恢复确认", build(39, 4))
        rows.append(("恢复确认", hexs(r) if r else "-", ""))

    # ---- 3. 按键/操作类：cid=33 ----
    for name, cmd, payload in [
        ("播放/暂停", 2, bytes([1])),
        ("下一曲", 5, b""),
        ("上一曲", 4, b""),
    ]:
        ack = await s.tx(f"音乐-{name}", build(33, cmd, payload), wait=1.0)
        rows.append((f"cid=33 cmd={cmd} {name}", hexs(ack) if ack else "(无回包)", ""))

    # ---- 4. 电量再读一次（看 NOTIFY 是否带来变化）----
    bat = await s.tx("GET_BATTERY", build(39, 1))
    rows.append(("GET_BATTERY", hexs(bat) if bat else "-", ""))

    log("")
    log("=" * 104)
    log(f"{'步骤':<26} {'回包':<48} 解读")
    log("=" * 104)
    for n, h, note in rows:
        log(f"{n:<26} {h:<48} {note}")

    log("")
    log(f"# NOTIFY (cid=40) 共 {len(s.notifies)} 条")
    for pkt in s.notifies:
        log(f"  {hexs(pkt)}")

    await s.close()

    d = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(d, "out"), exist_ok=True)
    path = os.path.join(d, "out", f"set4_{time.strftime('%Y%m%d_%H%M%S')}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# FIIL GATT 探测 v4  {datetime.now().isoformat(timespec='seconds')}\n")
        fh.write(f"# 设备 {dev.address}\n\n")
        fh.write("\n".join(LOG) + "\n")
    print(f"\n[+] 已保存 {path}")


if __name__ == "__main__":
    asyncio.run(main())
