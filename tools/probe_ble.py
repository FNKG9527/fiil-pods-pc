"""FIIL 耳机 BLE 探测 —— 订阅 Notify + 手动发 Hex，全程记录时间戳。

只做被动监听与最小探测帧写入（0x00 / 0xFF / 原样回显），不做破坏性写入。

用法:
    # 1) 静默监听 60 秒（只订阅，不发送任何字节）
    python probe_ble.py watch --seconds 60

    # 2) 监听 60 秒，然后在监听中手动输入 Hex 发送（交互式）
    python probe_ble.py session

    # 3) 直接发一串 Hex 并观察 5 秒回包
    python probe_ble.py send "00 FF" --seconds 5

    # 4) 只发到 ff16（默认）或 ff17
    python probe_ble.py send "00" --char ff17
"""

from __future__ import annotations

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime

from bleak import BleakClient, BleakScanner

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

SERVICE_FIIL = "0000fdb3-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY = "0000ff18-0000-1000-8000-00805f9b34fb"
CHAR_WRITE = "0000ff16-0000-1000-8000-00805f9b34fb"
CHAR_WRITE_NR = "0000ff17-0000-1000-8000-00805f9b34fb"

CHAR_ALIAS = {
    "ff16": CHAR_WRITE,
    "ff17": CHAR_WRITE_NR,
    "ff18": CHAR_NOTIFY,
}

FIIL_OUI_PREFIXES = ("B0:F1:A3", "FF:F1:A3")


def hexs(b: bytes) -> str:
    return b.hex(" ").upper()


def now_ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class Session:
    """一次连接会话，负责记录所有收发字节。"""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.started = time.time()

    def log(self, direction: str, char: str, payload: bytes, note: str = "") -> None:
        evt = {
            "t": round(time.time() - self.started, 3),
            "wall": now_ts(),
            "dir": direction,
            "char": char,
            "hex": hexs(payload),
            "len": len(payload),
            "note": note,
        }
        self.events.append(evt)
        arrow = "<<" if direction == "RX" else ">>"
        print(f"[{evt['wall']}] {arrow} {char:<11} {evt['hex']}  ({len(payload)}B) {note}")

    def save(self, tag: str) -> str:
        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(OUT_DIR, f"log_{tag}_{stamp}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# FIIL BLE 会话日志  {datetime.now().isoformat(timespec='seconds')}\n")
            fh.write("# t=相对秒  dir=RX(耳机->PC)/TX(PC->耳机)  char=特征值  hex=原始字节\n")
            for e in self.events:
                fh.write(
                    f"{e['t']:>8.3f}  {e['dir']}  {e['char']:<11}  {e['hex']:<40}  "
                    f"{e['len']:>3}B  {e['note']}\n"
                )
        print(f"\n[+] 日志已保存: {path}  （共 {len(self.events)} 条事件）")
        return path


async def find_fiil(timeout: float = 12.0):
    print(f"[*] 扫描 {timeout:.0f}s 寻找 FIIL 设备 ...")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for device, adv in found.values():
        if device.address.upper()[:8] in FIIL_OUI_PREFIXES:
            print(f"[+] 找到: {device.address}  name={device.name}  rssi={adv.rssi}")
            return device
    print("[!] 未找到 FIIL 设备（OUI B0:F1:A3 / FF:F1:A3）")
    return None


def make_handler(sess: Session):
    def _handler(_char, data: bytearray) -> None:
        sess.log("RX", "notify", bytes(data))

    return _handler


async def run_session(args) -> None:
    target = args.address
    if not target:
        dev = await find_fiil(args.scan)
        if dev is None:
            return
        target = dev.address

    sess = Session()
    print(f"[*] 连接 {target} ...")
    async with BleakClient(target, timeout=args.timeout) as client:
        print(f"[+] 已连接: {client.address}  mtu={getattr(client, 'mtu_size', '?')}")

        # 订阅所有 notify/indicate 特征值（本例只有 ff18）
        subs = []
        for svc in client.services:
            for ch in svc.characteristics:
                if "notify" in ch.properties or "indicate" in ch.properties:
                    await client.start_notify(ch, make_handler(sess))
                    subs.append(str(ch.uuid))
                    print(f"[+] 已订阅 {ch.uuid}  props={ch.properties}")

        if not subs:
            print("[!] 没有可订阅的 Notify 特征值")
            return

        print(f"[*] 静默监听 {args.seconds:.0f} 秒（期间不发送任何字节）...")
        print("    如果一直没有任何输出，请在耳机上操作一下（切换降噪 / 摘下耳机）。")
        deadline = time.time() + args.seconds
        while time.time() < deadline:
            await asyncio.sleep(0.2)
            if sess.events:
                # 有数据就不再干等，但继续听完剩余时间
                pass

        if not sess.events:
            print(f"[!] {args.seconds:.0f} 秒内没有收到任何 Notify。")
            print("    建议：在手机上用 FIIL+ APP 切一次降噪模式，然后重跑本脚本。")

        # 交互式手动发 Hex
        if args.interactive:
            print()
            print("=== 手动发送 Hex（回车=重发上一条，q=退出）===")
            print(f"    默认目标特征值: {args.char}（可用 ff16 / ff17 切换）")
            last = ""
            cur_char = CHAR_ALIAS.get(args.char.lower(), CHAR_WRITE)
            while True:
                try:
                    line = await asyncio.to_thread(input, "hex> ")
                except (EOFError, KeyboardInterrupt):
                    break
                line = line.strip()
                if line.lower() in ("q", "quit", "exit"):
                    break
                if line.lower().startswith("char "):
                    key = line.split(maxsplit=1)[1].strip().lower()
                    if key in CHAR_ALIAS:
                        cur_char = CHAR_ALIAS[key]
                        print(f"    -> 目标改为 {cur_char}")
                    continue
                if not line:
                    line = last
                if not line:
                    continue
                last = line
                try:
                    payload = bytes.fromhex(line.replace(" ", ""))
                except ValueError as exc:
                    print(f"    [!] Hex 解析失败: {exc}")
                    continue
                try:
                    await client.write_gatt_char(cur_char, payload, response=False)
                    sess.log("TX", cur_char[4:8], payload, note="manual")
                except Exception as exc:  # noqa: BLE001
                    print(f"    [!] 写入失败: {exc}")
                await asyncio.sleep(0.8)

        # 断开前清理订阅
        for uuid in subs:
            try:
                await client.stop_notify(uuid)
            except Exception:  # noqa: BLE001
                pass
        print("[*] 已清理 Notify 订阅")

    sess.save(args.tag)


async def run_send(args) -> None:
    target = args.address
    if not target:
        dev = await find_fiil(args.scan)
        if dev is None:
            return
        target = dev.address

    payload = bytes.fromhex(args.hex.replace(" ", ""))
    char = CHAR_ALIAS.get(args.char.lower(), CHAR_WRITE)

    sess = Session()
    print(f"[*] 连接 {target} ...")
    async with BleakClient(target, timeout=args.timeout) as client:
        print("[+] 已连接")
        for svc in client.services:
            for ch in svc.characteristics:
                if "notify" in ch.properties or "indicate" in ch.properties:
                    await client.start_notify(ch, make_handler(sess))
                    print(f"[+] 已订阅 {ch.uuid}")

        print(f"[*] 发送 {hexs(payload)} -> {char}")
        await client.write_gatt_char(char, payload, response=False)
        sess.log("TX", char[4:8], payload, note="probe")

        print(f"[*] 观察 {args.seconds:.0f} 秒 ...")
        await asyncio.sleep(args.seconds)

        for svc in client.services:
            for ch in svc.characteristics:
                if "notify" in ch.properties or "indicate" in ch.properties:
                    try:
                        await client.stop_notify(ch)
                    except Exception:  # noqa: BLE001
                        pass

    sess.save(args.tag)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="FIIL BLE 探测工具")
    ap.add_argument("mode", choices=["watch", "session", "send"], help="运行模式")
    ap.add_argument("hex", nargs="?", default="00", help="send 模式要发送的 Hex")
    ap.add_argument("--address", default=None, help="设备地址；省略则自动扫描")
    ap.add_argument("--scan", type=float, default=12.0)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--char", default="ff16", help="目标写入特征值: ff16 / ff17")
    ap.add_argument("--interactive", action="store_true", help="监听后进入手动发送")
    ap.add_argument("--tag", default="probe")
    return ap


def main() -> None:
    args = build_parser().parse_args()
    if args.mode == "send":
        asyncio.run(run_send(args))
    else:
        args.interactive = args.mode == "session"
        asyncio.run(run_session(args))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] 已中断")
