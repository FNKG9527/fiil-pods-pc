"""协议层单元自检（不连设备，纯字节级）。

运行：python -X utf8 tests/test_protocol.py   或   pytest tests/test_protocol.py
所有期望值都来自实机抓包（见 out/ 目录），不是推测。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fiil_protocol import (
    BatteryState,
    Cid,
    FiilProtocol,
    MafMode,
    ToneVolume,
    build_frame,
    parse_ascii,
    parse_bool,
    parse_frame,
    parse_uint,
    parse_version,
)

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        msg = f"  [FAIL] {name}\n         期望: {want!r}\n         实际: {got!r}"
        print(msg)
        FAILURES.append(msg)


def hexs(b: bytes) -> str:
    return b.hex(" ").upper()


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# ---------------------------------------------------------------------------
# 1. 组帧
# ---------------------------------------------------------------------------
section("1. 组帧（与 com.fengeek.main.i.b.writeMate 逐字节对齐）")

check("setUnlock", hexs(build_frame(39, 0xFF, bytes([2, 0x34]))),
      "00 27 01 00 04 FF 02 02 34")
check("GET_VERSION", hexs(build_frame(39, 2)), "00 27 01 00 02 02 00")
check("GET_BATTERY", hexs(FiilProtocol().request_battery()), "00 27 01 00 02 01 00")
check("GET_MAF", hexs(FiilProtocol().request_maf()), "00 27 01 00 02 0C 00")
check("GET_SPATIAL", hexs(FiilProtocol().request_cinema()), "00 27 01 00 02 18 00")
check("GET_EQ", hexs(FiilProtocol().request_eq()), "00 27 01 00 02 04 00")
# SET：cid=命令族，cmdId=0，长度字段就是 payloadLen
check("SET_MAF=1 (降噪)", hexs(FiilProtocol().set_maf(MafMode.NOISE_CANCEL)),
      "00 2C 01 00 01 01")
check("SET_MAF=0 (关闭)", hexs(build_frame(int(Cid.SET_MAF), 0, b"\x00")),
      "00 2C 01 00 01 00")
check("SET_SPATIAL=1", hexs(FiilProtocol().set_cinema(True)), "00 32 01 00 01 01")
check("SET_LATENCY=1", hexs(FiilProtocol().set_low_latency(1)), "00 25 01 00 01 01")
check("SET_EQ 12B", hexs(FiilProtocol().set_eq(bytes(range(12)))),
      "00 20 01 00 0C 00 01 02 03 04 05 06 07 08 09 0A 0B")
# SET 畅连：实机 8 字节 `xx 01 01 00 03 01 01 <0/1>`（payload 只有 1 字节）
# 注意：新建的 FiilProtocol 实例 seq 从 0 起，所以首字节都是 00，不是实机抓到的 seq。
check("SET_MP=0 (实机帧)", hexs(FiilProtocol().set_multipoint(False)),
      "00 01 01 00 03 01 01 00")
check("SET_MP=1 (实机帧)", hexs(FiilProtocol().set_multipoint(True)),
      "00 01 01 00 03 01 01 01")
# GET_KEY(5) 是按键配置表，不是 earType
check("GET_KEY(5)", hexs(FiilProtocol().request_key_config()), "00 27 01 00 02 05 00")
check("GET_CHANGLAN(128)", hexs(FiilProtocol().request_multipoint()), "00 27 01 00 02 80 00")
check("GET_LOW_LATENCY(8)", hexs(FiilProtocol().request_low_latency()), "00 27 01 00 02 08 00")
check("GET_TONE(130)", hexs(FiilProtocol().request_tone_volume()), "00 27 01 00 02 82 00")
# SET 语音提示音量：cid=1 (SET_PARAM), cmdId=3，payload 1 字节
# 实机帧：`xx 01 01 00 03 03 01 02`（音量低）
for v, hexv in ((0, "00"), (1, "01"), (2, "02"), (3, "03")):
    check(f"SET_TONE={v}", hexs(FiilProtocol().set_tone_volume(v)),
          f"00 01 01 00 03 03 01 {hexv}")
check("ToneVolume.HIGH 线值", int(ToneVolume.HIGH), 0)
check("ToneVolume.MUTE 线值", int(ToneVolume.MUTE), 3)
check("ToneVolume.LOW 文案", ToneVolume.LOW.label_cn, "音量低")

# seq 滚动
p = FiilProtocol()
seqs = [p.request_battery()[0] for _ in range(4)]
check("seq 滚动 0,1,2,3", seqs, [0, 1, 2, 3])


# ---------------------------------------------------------------------------
# 2. 解帧
# ---------------------------------------------------------------------------
section("2. 解帧（实机抓包样本）")

f = parse_frame(bytes.fromhex("02270200050103005000".replace(" ", "")))
check("GET_BATTERY 回包 cid", f.cid, 39)
check("GET_BATTERY 回包 cmd", f.cmd, 1)
check("GET_BATTERY 回包 payload", hexs(f.payload), "00 50 00")

f = parse_frame(bytes.fromhex("012702000602040300010 1".replace(" ", "")))
check("GET_VERSION payload", hexs(f.payload), "03 00 01 01")
check("GET_VERSION 解析", parse_version(f.payload), "3.0.1.1")

f = parse_frame(bytes.fromhex("0A2702000E030C4649494C204B6579204D6178".replace(" ", "")))
check("GET_NAME 解析", parse_ascii(f.payload), "FIIL Key Max")

# SET 应答：6 字节，lenField=1，payload[0]=0 成功
for name, raw in (
    ("SET_MAF ack", "022c02000100"),
    ("SET_SPATIAL ack", "0a3202000100"),
    ("SET_LATENCY ack", "002502000100"),
):
    fa = parse_frame(bytes.fromhex(raw))
    check(f"{name} 帧长", len(fa.raw), 6)
    check(f"{name} ok", fa.ok, True)
    check(f"{name} payload", hexs(fa.payload), "00")

# 真·空帧（不认识的命令）
fe = parse_frame(bytes.fromhex("0827020000".replace(" ", "")))
check("空帧 cmd", fe.cmd, None)
check("空帧 payload", fe.payload, b"")

# NOTIFY（cid=40）
fn = parse_frame(bytes.fromhex("04280300030c0100".replace(" ", "")))
check("NOTIFY cid", fn.cid, 40)
check("NOTIFY is_notify", fn.is_notify, True)
check("NOTIFY cmd", fn.cmd, 0x0C)


# ---------------------------------------------------------------------------
# 3. 流式重组
# ---------------------------------------------------------------------------
section("3. 流式重组 / 粘包 / 半包 / 重同步")

full = bytes.fromhex("042702000E040C0A0E0806040200 01FAFCFE02".replace(" ", ""))
pr = FiilProtocol()
half = len(full) // 2
check("半包前半 → 无帧", pr.feed(full[:half]), [])
fr = pr.feed(full[half:])
check("半包后半 → 1 帧", len(fr), 1)
check("半包重组 payload 长度", len(fr[0].payload), 12)

pr = FiilProtocol()
two = bytes.fromhex("02270200050103005000") + bytes.fromhex("03270200030C0101")
check("粘包 → 2 帧", len(pr.feed(two)), 2)

pr = FiilProtocol()
frames = pr.feed(b"\x99\x88" + bytes.fromhex("02270200050103005000"))
check("垃圾前缀重同步", len(frames), 1)

pr = FiilProtocol()
out = []
for b in full:
    out += pr.feed(bytes([b]))
check("逐字节喂入", len(out), 1)

# SET 应答与 GET 应答混在一起（真实场景：SET 后紧跟 GET）
pr = FiilProtocol()
mix = (bytes.fromhex("022c02000100") + bytes.fromhex("03270200030c0100"))
fs = pr.feed(mix)
check("SET ack + GET 混流 → 2 帧", len(fs), 2)
check("第一帧是 SET ack", (len(fs[0].raw), fs[0].cid), (6, 0x2C))
check("第二帧是 GET", fs[1].cmd, 0x0C)


# ---------------------------------------------------------------------------
# 4. 电池 / 各命令解析
# ---------------------------------------------------------------------------
section("4. 电池与其他解析")

# 实机：payload = 00 4D 00，官方源码 F046 取 payload[1]（单电池机型）
b_f46 = BatteryState.parse(bytes.fromhex("004D00"), ear_type=46)
check("Key Max(F046) 单电池 77%", b_f46.single, 0x4D)
check("Key Max 不误标成右耳", b_f46.right, None)
check("Key Max 显示", b_f46.display(), "耳机 77%")

# 型号未知时按三值解析（回包另外两字节本来就是 0 → --）
b = BatteryState.parse(bytes.fromhex("005000"))
check("电池 80%（型号未知按三值）", b.right, 0x50)
check("电池显示", b.display(), "左 -- / 右 80% / 盒 --")

# F051/F053 三值
b3 = BatteryState.parse(bytes.fromhex("4B5A0A"))
check("三耳机电量 left", b3.left, 0x4B)
check("三耳机电量 right", b3.right, 0x5A)
check("三耳机电量 case", b3.case, 0x0A)
check("三耳机显示", b3.display(), "左 75% / 右 90% / 盒 10%")

# F045/F047/F049 也是三值，不能被 SINGLE_CELL 误判
for et in (45, 47, 48, 49, 51, 53):
    bx = BatteryState.parse(bytes.fromhex("4B5A0A"), ear_type=et)
    check(f"earType {et} 仍按三值解析", (bx.left, bx.right, bx.case), (0x4B, 0x5A, 0x0A))

# 0 / 255 视为无数据
check("0 → None", BatteryState.parse(bytes.fromhex("000000")).right, None)
check("255 → None", BatteryState.parse(bytes.fromhex("00FF00")).right, None)
check("F046 0 → None", BatteryState.parse(bytes.fromhex("000000"), ear_type=46).single, None)
check("F046 255 → None", BatteryState.parse(bytes.fromhex("00FF00"), ear_type=46).single, None)

check("MAF=1 降噪", MafMode(1).label_cn, "降噪")
check("MAF=2 开放", MafMode(2).label_cn, "开放")
check("MAF=3 兼听", MafMode(3).label_cn, "兼听")
check("MAF=4 风中", MafMode(4).label_cn, "风中")
check("bool 0", parse_bool(b"\x00"), False)
check("bool 1", parse_bool(b"\x01"), True)
check("uint", parse_uint(b"\x0e"), 14)


# ---------------------------------------------------------------------------
# 5. 坏输入不崩
# ---------------------------------------------------------------------------
section("5. 坏输入健壮性")

for raw in ("", "00", "0027", "002701", "00270100"):
    check(f"短包 {raw!r} 返回 None", parse_frame(bytes.fromhex(raw) if raw else b""), None)

# reserved 非 0 → 拒
check("reserved!=0 拒", parse_frame(bytes.fromhex("00270101020200")), None)
# type 非法 → 拒
check("type=0x09 拒", parse_frame(bytes.fromhex("00270900020200")), None)

# 随机字节流不抛异常
import os  # noqa: E402

pr = FiilProtocol()
try:
    for _ in range(200):
        pr.feed(os.urandom(7))
    ok = True
except Exception as e:  # noqa: BLE001
    ok = False
    print(f"       异常：{e}")
check("随机字节流不抛异常", ok, True)


# ---------------------------------------------------------------------------
# 上面的 check() 在模块导入时就跑完了（纯字节比对，不连设备）。
# pytest 收集本模块时会执行一次，再用 test_all() 断言结果。
def test_all() -> None:
    """pytest 入口：断言全部字节级检查通过。"""
    assert FAIL == 0, f"{FAIL} 项失败：\n" + "\n".join(FAILURES)


def main() -> int:
    """命令行入口。"""
    section(f"结果：{PASS} 通过 / {FAIL} 失败")
    if FAIL:
        print("\n失败明细：")
        for m in FAILURES:
            print(m)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
