"""FIIL GATT 全表探测 —— 逐条发送 Mate 查询帧并记录回包。

依据：`docs/PROTOCOL_FIIL.md` 与 FIIL+ APP 逆向结论（Mate 帧格式）：
    [seq][cid][type][00][lenField][cmd][payloadLen][payload...]
    cid=0x27(39) 命令通道，type=0x01 REQUEST / 0x02 RESPONSE / 0x03 NOTIFY

本脚本会先发 setUnlock，再逐条发查询，每条之间间隔 0.5s，全部记录。
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

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
CHAR_NOTIFY = "0000ff18-0000-1000-8000-00805f9b34fb"
CHAR_WRITE = "0000ff17-0000-1000-8000-00805f9b34fb"
FIIL_OUI = ("B0:F1:A3", "FF:F1:A3")

SEQ = 0


def hexs(b: bytes) -> str:
    return b.hex(" ").upper()


def build(cid: int, cmd: int, payload: bytes = b"", seq: int | None = None) -> bytes:
    """构造 Mate 帧。cmd=0 表示无命令字的短帧。"""
    global SEQ
    s = SEQ & 0x0F if seq is None else seq
    SEQ = (SEQ + 1) & 0x0F
    if cmd == 0:
        payload_len = len(payload)
        return bytes([s, cid, 0x01, 0x00, payload_len + 2]) + payload
    payload_len = len(payload)
    return bytes([s, cid, 0x01, 0x00, payload_len + 2, cmd, payload_len]) + payload


# (名称, cid, cmd, payload, 说明)
QUERIES = [
    ("setUnlock", 39, 0xFF, bytes([0x02, 0x34]), "解锁握手（必须先发）"),
    ("GET_VERSION", 39, 0x02, b"", "固件版本"),
    ("GET_BATTERY", 39, 0x01, b"", "电量"),
    ("GET_MAF", 39, 0x0C, b"", "MAF 降噪模式"),
    ("GET_LOW_LATENCY", 39, 0x08, b"", "低延迟"),
    ("GET_MULTIPOINT", 39, 0x80, b"", "多点连接"),
    ("GET_CODEC", 39, 0x90, b"", "编码格式"),
    ("GET_EQ", 39, 0x04, b"", "EQ 预设/自定义"),
    ("GET_CINEMA", 39, 0x0B, b"", "影院模式（待确认 cmd）"),
    ("GET_WEAR", 39, 0x20, b"", "佩戴检测（待确认 cmd）"),
    ("GET_ADDRESS", 39, 0x03, b"", "设备地址"),
    ("GET_EARTYPE", 39, 0x05, b"", "机型 earType"),
]


async def main() -> None:
    timeout = 12.0
    print("[*] 扫描 FIIL 设备 ...")
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    dev = None
    for d, adv in found.values():
        if d.address.upper()[:8] in FIIL_OUI:
            dev = d
            break
    if dev is None:
        print("[!] 未找到设备")
        return
    print(f"[+] {dev.address}")

    events: list[dict] = []
    t0 = time.time()

    def on_notify(_c, data: bytearray) -> None:
        evt = {
            "t": round(time.time() - t0, 3),
            "wall": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "hex": hexs(bytes(data)),
            "len": len(data),
        }
        events.append(evt)
        print(f"    << [{evt['wall']}] {evt['hex']}  ({evt['len']}B)")

    async with BleakClient(dev.address, timeout=timeout) as client:
        print(f"[+] 已连接 mtu={getattr(client, 'mtu_size', '?')}")
        await client.start_notify(CHAR_NOTIFY, on_notify)
        print("[+] 已订阅 ff18")
        print()

        results: list[tuple[str, str, str, str]] = []

        for name, cid, cmd, payload, note in QUERIES:
            frame = build(cid, cmd, payload)
            before = len(events)
            print(f"[TX] {name:<18} {hexs(frame)}   ({note})")
            try:
                await client.write_gatt_char(CHAR_WRITE, frame, response=False)
            except Exception as exc:  # noqa: BLE001
                print(f"    [!] 写入失败: {exc}")
                results.append((name, hexs(frame), "(写入失败)", note))
                continue
            await asyncio.sleep(0.6)
            new = events[before:]
            rx = " | ".join(e["hex"] for e in new) if new else "(无响应)"
            results.append((name, hexs(frame), rx, note))
            await asyncio.sleep(0.3)

        print()
        print("=" * 110)
        print(f"{'命令':<18} {'发送':<34} {'回包':<44} 说明")
        print("=" * 110)
        for name, tx, rx, note in results:
            print(f"{name:<18} {tx:<34} {rx:<44} {note}")

        # 保存
        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(OUT_DIR, f"table_{stamp}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# FIIL GATT 全表探测  {datetime.now().isoformat(timespec='seconds')}\n")
            fh.write(f"# 设备 {dev.address}  write={CHAR_WRITE}  notify={CHAR_NOTIFY}\n\n")
            fh.write(f"{'命令':<18}\t{'发送':<34}\t{'回包':<44}\t说明\n")
            for name, tx, rx, note in results:
                fh.write(f"{name:<18}\t{tx:<34}\t{rx:<44}\t{note}\n")
            fh.write("\n# 完整原始事件流\n")
            for e in events:
                fh.write(f"{e['t']:>8.3f}  RX  {e['hex']:<40}  {e['len']:>3}B\n")
        print(f"\n[+] 已保存 {path}")

        try:
            await client.stop_notify(CHAR_NOTIFY)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    asyncio.run(main())
