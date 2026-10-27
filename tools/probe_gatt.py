"""FIIL 耳机 BLE（GATT）枚举探针 —— 只做扫描与枚举，不写入任何数据。

用法:
    python probe_gatt.py            # 扫描 12 秒并列出所有含 FIIL 的设备
    python probe_gatt.py --all      # 列出全部 BLE 设备
    python probe_gatt.py --connect <address|name>   # 连接并枚举 GATT 树

输出同时写入 out/gatt_<时间戳>.json 与 out/gatt_<时间戳>.txt
"""

from __future__ import annotations

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

# Windows 控制台默认 GBK，中文会变乱码；强制 UTF-8 输出。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

FIIL_TOKENS = ("FIIL", "KEY MAX", "CC PRO", "GS LINKS", "ATOM", "CARAT")

# FIIL 的蓝牙 MAC OUI 前缀（逆向自 FIIL+ APP 的 FiilConfig.FIIL_OUI_PREFIXES）。
# 耳机常常在 BLE 广播里不带名字，只能靠 OUI 认出来。
FIIL_OUI_PREFIXES = ("B0:F1:A3", "FF:F1:A3")


def is_fiil_mac(address: str | None) -> bool:
    if not address:
        return False
    return address.upper()[:8] in FIIL_OUI_PREFIXES


def is_fiil(name: str | None, address: str | None = None) -> bool:
    if is_fiil_mac(address):
        return True
    if not name:
        return False
    upper = name.upper()
    return any(token in upper for token in FIIL_TOKENS)


def esc(b: bytes) -> str:
    return b.hex(" ").upper()


async def scan(seconds: float, show_all: bool) -> list[tuple[BLEDevice, int]]:
    print(f"[*] 扫描 BLE 设备 {seconds:.0f} 秒 ...")
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    rows: list[tuple[BLEDevice, int]] = []
    for device, adv in found.values():
        rows.append((device, adv.rssi))
    rows.sort(key=lambda r: -r[1])
    print(f"[*] 共发现 {len(rows)} 个 BLE 设备")
    print()
    header = f"{'地址':<20} {'RSSI':>5}  {'名称'}"
    print(header)
    print("-" * len(header))
    for device, rssi in rows:
        name = device.name or "(无名)"
        if show_all or is_fiil(device.name, device.address):
            mark = "  <== FIIL" if is_fiil(device.name, device.address) else ""
            print(f"{device.address:<20} {rssi:>5}  {name}{mark}")
    print()
    print("提示：耳机若已被手机 APP 独占连接，PC 端可能扫不到或连不上。")
    print("      请先断开手机上的 FIIL+ APP，并确保耳机处于配对/可连接状态。")
    return rows


async def enumerate_gatt(target: str, timeout: float) -> dict:
    print(f"[*] 连接 {target} ...")
    async with BleakClient(target, timeout=timeout) as client:
        if not client.is_connected:
            print("[!] 连接失败")
            return {}
        print(f"[+] 已连接: {client.address}")
        print()

        services = client.services
        dump: dict = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "address": client.address,
            "mtu": getattr(client, "mtu_size", None),
            "services": [],
        }

        for svc in services:
            svc_row = {
                "uuid": str(svc.uuid),
                "description": svc.description,
                "characteristics": [],
            }
            print(f"Service {svc.uuid}  [{svc.description}]")
            for ch in svc.characteristics:
                props = ",".join(ch.properties)
                ch_row = {
                    "uuid": str(ch.uuid),
                    "handle": ch.handle,
                    "properties": ch.properties,
                    "descriptors": [],
                }
                print(f"  Char {ch.uuid}  handle={ch.handle}  props=[{props}]")
                for desc in ch.descriptors:
                    desc_row = {
                        "uuid": str(desc.uuid),
                        "handle": desc.handle,
                        "description": getattr(desc, "description", ""),
                    }
                    # 用户描述符 0x2901 里是人可读的名称，很关键
                    if str(desc.uuid).lower().startswith("00002901"):
                        try:
                            raw = await client.read_gatt_descriptor(desc.handle)
                            desc_row["value_hex"] = esc(raw)
                            desc_row["value_text"] = raw.decode("utf-8", "replace")
                            print(
                                f"    Desc(0x2901) handle={desc.handle} "
                                f"-> {desc_row['value_text']!r}  [{esc(raw)}]"
                            )
                        except Exception as exc:  # noqa: BLE001
                            desc_row["error"] = repr(exc)
                            print(f"    Desc(0x2901) handle={desc.handle} 读取失败: {exc}")
                    else:
                        print(f"    Desc {desc.uuid}  handle={desc.handle}")
                    ch_row["descriptors"].append(desc_row)

                # 可读特征值顺手读一次（只读，不写）
                if "read" in ch.properties:
                    try:
                        raw = await asyncio.wait_for(
                            client.read_gatt_char(ch), timeout=timeout
                        )
                        ch_row["read_value_hex"] = esc(raw)
                        print(f"    read -> {esc(raw)}")
                    except Exception as exc:  # noqa: BLE001
                        ch_row["read_error"] = repr(exc)
                        print(f"    read 失败: {exc}")

                svc_row["characteristics"].append(ch_row)
            dump["services"].append(svc_row)
            print()

        return dump


def save(dump: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(OUT_DIR, f"gatt_{stamp}.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(dump, fh, ensure_ascii=False, indent=2)
    print(f"[+] GATT 树已保存: {json_path}")

    txt_path = os.path.join(OUT_DIR, f"gatt_{stamp}.txt")
    with open(txt_path, "w", encoding="utf-8") as fh:
        for svc in dump["services"]:
            fh.write(f"Service {svc['uuid']}  [{svc['description']}]\n")
            for ch in svc["characteristics"]:
                fh.write(
                    f"  Char {ch['uuid']} handle={ch['handle']} "
                    f"props=[{','.join(ch['properties'])}]\n"
                )
                if "read_value_hex" in ch:
                    fh.write(f"    read = {ch['read_value_hex']}\n")
                for desc in ch["descriptors"]:
                    fh.write(f"    Desc {desc['uuid']} handle={desc['handle']}")
                    if "value_text" in desc:
                        fh.write(f"  -> {desc['value_text']!r} [{desc['value_hex']}]")
                    fh.write("\n")
            fh.write("\n")
    print(f"[+] 文本版: {txt_path}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", type=float, default=12.0, help="扫描秒数")
    ap.add_argument("--all", action="store_true", help="列出全部 BLE 设备")
    ap.add_argument("--connect", default=None, help="要枚举 GATT 的设备地址或名称")
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args()

    rows = await scan(args.scan, args.all)

    target = args.connect
    if target is None:
        fiil = [d for d, _ in rows if is_fiil(d.name, d.address)]
        if fiil:
            target = fiil[0].address
            print(f"[*] 自动选中第一个 FIIL 设备: {target} ({fiil[0].name})")
        else:
            print("[!] 没有发现 FIIL 设备，跳过 GATT 枚举。")
            return

    dump = await enumerate_gatt(target, args.timeout)
    if dump:
        save(dump)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] 已中断")
        sys.exit(1)
