"""补测：把 jadx 里被 NetError 常量名掩盖的 cmdId 全部实机验证一遍。

对应源码：com/fengeek/main/i/d/c.java（全部 getter，cid=39）
  getChangLanStatus      = -128 = 128 (0x80)
  getCodecStatus         = ERR_NO_SSL_VERSIONS_ENABLED        = -112 = 144 (0x90)
  getEQStatus            = 4
  getElectricity         = 1
  getKey                 = 5      ← 按键配置表（不是 earType！）
  getLdacSwitchStatus    = ERR_SSL_VERSION_OR_CIPHER_MISMATCH = -113 = 143 (0x8F)
  getLdacUnlockStatus    = ERR_SSL_RENEGOTIATION_REQUESTED    = -114 = 142 (0x8E)
  getLeftHeadsetUUID     = ERR_CONNECTION_TIMED_OUT           = -118 = 138 (0x8A)
  getLowDelayStatus      = 8
  getMAFStatus           = 12
  getMusicStatus         = 7
  getPID                 = 132 (0x84)
  getRightHeadsetUUID    = ERR_BAD_SSL_CLIENT_AUTH_CERT       = -117 = 139 (0x8B)
  getSpatialAudioStatus  = 24
  getToneStatus          = ERR_SSL_BAD_RECORD_MAC_ALERT       = -126 = 130 (0x82)
  getVersion             = 2
"""

from __future__ import annotations

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import asyncio
import datetime as dt
from pathlib import Path

from bleak import BleakClient, BleakScanner

from fiil_protocol import (
    CHAR_NOTIFY_UUID,
    CHAR_WRITE_UUID,
    build_frame,
    drain,
)

DEVICE_NAME_HINT = "FIIL"
# 实机已知地址（名字在 discover 里常为 None，改按地址匹配）
DEVICE_ADDRESS = "B0:F1:A3:00:00:01"
OUT_DIR = Path(__file__).parent.parent / "out"

# (显示名, cmdId, 源码方法)
PROBES = [
    ("getChangLanStatus", 128, "c.java:27"),
    ("getCodecStatus", 144, "c.java:43 (NetError -112)"),
    ("getEQStatus", 4, "c.java:59"),
    ("getElectricity", 1, "c.java:75"),
    ("getKey", 5, "c.java:91"),
    ("getLdacSwitchStatus", 143, "c.java:107 (NetError -113)"),
    ("getLdacUnlockStatus", 142, "c.java:123 (NetError -114)"),
    ("getLeftHeadsetUUID", 138, "c.java:139 (NetError -118)"),
    ("getLowDelayStatus", 8, "c.java:155"),
    ("getMAFStatus", 12, "c.java:171"),
    ("getMusicStatus", 7, "c.java:187"),
    ("getPID", 132, "c.java:203"),
    ("getRightHeadsetUUID", 139, "c.java:219 (NetError -117)"),
    ("getSpatialAudioStatus", 24, "c.java:235"),
    ("getToneStatus", 130, "c.java:251 (NetError -126)"),
    ("getVersion", 2, "c.java:267"),
]


class Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, s: str) -> None:
        print(s)
        self.lines.append(s)

    def save(self, tag: str) -> Path:
        OUT_DIR.mkdir(exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        p = OUT_DIR / f"{tag}_{ts}.txt"
        p.write_text("\n".join(self.lines), encoding="utf-8")
        return p


def fmt(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


async def main() -> int:
    log = Log()
    log.add("=" * 78)
    log.add(f"# 未确认 cmdId 补测  {dt.datetime.now():%Y-%m-%dT%H:%M:%S}")
    log.add("=" * 78)

    log.add("[*] 扫描中…")
    devices = await BleakScanner.discover(timeout=12.0)
    target = None
    for d in devices:
        name = d.name or ""
        if d.address.upper() == DEVICE_ADDRESS.upper():
            target = d
            break
        if target is None and DEVICE_NAME_HINT.upper() in name.upper():
            target = d
    if target is None:
        log.add(f"[!] 未找到 {DEVICE_ADDRESS}。已发现：")
        for d in devices:
            log.add(f"      {d.address}  {d.name}")
        log.save("unverified")
        return 1

    log.add(f"[+] 目标 {target.address}  {target.name}")

    rx: list[bytes] = []

    def on_notify(_ch, data: bytearray) -> None:
        rx.append(bytes(data))

    async with BleakClient(target) as client:
        log.add(f"[+] 已连接，MTU={getattr(client, 'mtu_size', '?')}")
        await client.start_notify(CHAR_NOTIFY_UUID, on_notify)

        # 握手
        unlock = build_frame(39, 0xFF, bytes([0x02, 0x34]))
        log.add(f"[TX] setUnlock                {fmt(unlock)}")
        await client.write_gatt_char(CHAR_WRITE_UUID, unlock, response=False)
        await asyncio.sleep(0.6)
        while rx:
            for f in drain(rx.pop(0))[0]:
                log.add(f"     [RX] {fmt(f.raw):<44} {f.describe()}")

        seq = 1
        for name, cmd, src in PROBES:
            frame = build_frame(39, cmd, b"", seq=seq & 0x0F)
            seq += 1
            log.add(f"[TX] {name:<22} {fmt(frame)}    ({src})")
            await client.write_gatt_char(CHAR_WRITE_UUID, frame, response=False)
            await asyncio.sleep(0.75)
            got = False
            while rx:
                for f in drain(rx.pop(0))[0]:
                    log.add(f"     [RX] {fmt(f.raw):<44} {f.describe()}")
                    got = True
            if not got:
                log.add("     [RX] (无响应)")

        await client.stop_notify(CHAR_NOTIFY_UUID)
        log.add("[+] 已断开")

    p = log.save("unverified_cmdid")
    log.add(f"[+] 已保存 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
