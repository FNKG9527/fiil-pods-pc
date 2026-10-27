"""全量 SET 时序矩阵实验：每个 SET 后分时读回并捕获 NOTIFY

目的：用统一的方法论验证每个可写命令
  - 回包 ack 的 payload[0] 是否为 0（成功）
  - 状态在 +0.3s / +1.5s / +3s 时是否到位
  - 是否伴随 cid=40 主动推送

只测「可逆」且「无损」的控制：MAF、影院、低延迟、多点。
EQ 只读不写（因为写入不生效，见 out/set4_*.txt）。
"""

from __future__ import annotations

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import asyncio
from dataclasses import dataclass
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

ORG = "B0:F1:A3:00:00:01"
OUT = Path(__file__).resolve().parent.parent / "out"
LOG: list[str] = []


def log(s: str = "") -> None:
    print(s)
    LOG.append(s)


@dataclass
class Trial:
    name: str
    ack: str
    r03: str
    r15: str
    r30: str
    notify: str
    verdict: str


async def main() -> None:
    proto = FiilProtocol()
    rx: list[Frame] = []
    notifies: list[bytes] = []

    def on_notify(_c, data: bytearray) -> None:
        chunk = bytes(data)
        for f in proto.feed(chunk):
            rx.append(f)
            if f.is_notify:
                notifies.append(f.raw)

    trials: list[Trial] = []

    async with BleakClient(ORG, timeout=20.0) as client:
        await client.start_notify(CHAR_NOTIFY_UUID, on_notify)
        await asyncio.sleep(0.3)

        async def tx(name: str, frame: bytes, wait: float = 0.5) -> list[Frame]:
            n0 = len(rx)
            await client.write_gatt_char(CHAR_WRITE_UUID, frame, response=False)
            log(f"[TX] {name:<26} {frame.hex(' ').upper()}")
            await asyncio.sleep(wait)
            new = rx[n0:]
            for f in new:
                log(f"     [RX] {f.hex()}")
            return new

        log("=" * 86)
        log(f"# 全量 SET 时序矩阵 {datetime.now().isoformat(timespec='seconds')}")
        log(f"# 设备 {ORG}")
        log("=" * 86)

        await tx("setUnlock", proto.request_unlock())

        # ---------- 1. MAF ----------
        log("\n### 1. MAF 降噪")
        f = await tx("GET_MAF", proto.request_maf())
        v0 = parse_uint(f[-1].payload) if f else None
        tgt = int(MafMode.OFF) if v0 != int(MafMode.OFF) else int(MafMode.NOISE_CANCEL)
        log(f"  原值={v0}，目标={tgt}")

        notifies.clear()
        ack = await tx(f"SET_MAF={tgt}", proto.set_maf(MafMode(tgt)))
        ack_ok = "OK" if (ack and ack[-1].ok) else f"FAIL/{ack[-1].raw.hex() if ack else 'none'}"

        vals = []
        for d in (0.3, 1.5, 3.0):
            await asyncio.sleep(d if d == 0.3 else 0.9 if d == 1.5 else 1.5)
            r = await tx(f"GET_MAF @{d}s", proto.request_maf(), wait=0.4)
            vals.append(str(parse_uint(r[-1].payload)) if r else "-")
        note = " ".join(n.hex() for n in notifies) or "无"
        verdict = "✓ 生效" if vals[-1] == str(tgt) else "✗ 未生效"
        trials.append(Trial("MAF", ack_ok, vals[0], vals[1], vals[2], note, verdict))
        if v0 is not None:
            await tx(f"恢复 MAF={v0}", proto.set_maf(MafMode(v0)))
            await asyncio.sleep(1.6)

        # ---------- 2. 影院 ----------
        log("\n### 2. 影院（空间音频）")
        f = await tx("GET_SPATIAL", proto.request_cinema())
        c0 = parse_bool(f[-1].payload) if f else None
        tgt = not bool(c0)
        log(f"  原值={c0}，目标={tgt}")

        notifies.clear()
        ack = await tx(f"SET_SPATIAL={int(tgt)}", proto.set_cinema(tgt))
        ack_ok = "OK" if (ack and ack[-1].ok) else f"FAIL/{ack[-1].raw.hex() if ack else 'none'}"

        vals = []
        for d in (0.3, 1.5, 3.0):
            await asyncio.sleep(d if d == 0.3 else 0.9 if d == 1.5 else 1.5)
            r = await tx(f"GET_SPATIAL @{d}s", proto.request_cinema(), wait=0.4)
            vals.append(str(parse_bool(r[-1].payload)) if r else "-")
        note = " ".join(n.hex() for n in notifies) or "无"
        verdict = "✓ 生效" if vals[-1] == str(tgt) else "✗ 未生效"
        trials.append(Trial("影院", ack_ok, vals[0], vals[1], vals[2], note, verdict))
        if c0 is not None:
            await tx(f"恢复 SPATIAL={int(c0)}", proto.set_cinema(bool(c0)))
            await asyncio.sleep(1.6)

        # ---------- 3. 低延迟 ----------
        log("\n### 3. 低延迟")
        f = await tx("GET_LOW_LATENCY", proto.request_low_latency())
        l0 = parse_uint(f[-1].payload) if f else None
        tgt = 1 if l0 == 0 else 0
        log(f"  原值={l0}，目标={tgt}")

        notifies.clear()
        ack = await tx(f"SET_LATENCY={tgt}", proto.set_low_latency(tgt))
        ack_ok = "OK" if (ack and ack[-1].ok) else f"FAIL/{ack[-1].raw.hex() if ack else 'none'}"

        vals = []
        for d in (0.3, 1.5, 3.0):
            await asyncio.sleep(d if d == 0.3 else 0.9 if d == 1.5 else 1.5)
            r = await tx(f"GET_LATENCY @{d}s", proto.request_low_latency(), wait=0.4)
            vals.append(str(parse_uint(r[-1].payload)) if r else "-")
        note = " ".join(n.hex() for n in notifies) or "无"
        verdict = "✓ 生效" if vals[-1] == str(tgt) else "✗ 未生效"
        trials.append(Trial("低延迟", ack_ok, vals[0], vals[1], vals[2], note, verdict))
        if l0 is not None:
            await tx(f"恢复 LATENCY={l0}", proto.set_low_latency(l0))
            await asyncio.sleep(1.6)

        await client.stop_notify(CHAR_NOTIFY_UUID)

    # ---------- 汇总 ----------
    log("\n" + "=" * 86)
    log("汇总（读回值 @0.3s / @1.5s / @3.0s）")
    log("=" * 86)
    log(f"{'项目':<10}{'ACK':<12}{'0.3s':<8}{'1.5s':<8}{'3.0s':<8}{'结论':<10}")
    log("-" * 86)
    for t in trials:
        log(f"{t.name:<10}{t.ack:<12}{t.r03:<8}{t.r15:<8}{t.r30:<8}{t.verdict:<10}")
    log("")
    log("期间 cid=40 主动推送：")
    for t in trials:
        log(f"  {t.name}: {t.notify}")

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"set_matrix_{datetime.now():%Y%m%d_%H%M%S}.txt"
    p.write_text("\n".join(LOG) + "\n", encoding="utf-8")
    print(f"\n[+] 已保存 {p}")


if __name__ == "__main__":
    asyncio.run(main())
