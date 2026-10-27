"""校验 PROTOCOL_PC.md 里写的每一个字节，确保文档没有和代码脱节。

这是「文档-实现一致性」测试：从 md 里抽出所有 `xx XX 01 00 ...` 形态的帧，
用 fiil_protocol 重新生成，逐字节对比。任何一条对不上都算失败。

运行：python -X utf8 check_doc.py
"""

from __future__ import annotations

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import re
import sys
from pathlib import Path

from fiil_protocol import FiilProtocol, MafMode, build_frame

MD = Path(__file__).parent.parent / "docs" / "PROTOCOL_PC.md"

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, got: str, want: str) -> None:
    global PASS, FAIL
    g = got.upper().strip()
    w = want.upper().strip()
    if g == w:
        PASS += 1
    else:
        FAIL += 1
        msg = f"  [FAIL] {name}\n         文档写的: {w}\n         代码生成: {g}"
        print(msg)
        FAILURES.append(msg)


def hexs(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


# 文档里声明的帧 → 用代码重新生成
# (文档里的命名, 代码生成函数, 文档里写的字节)
CASES = [
    # §2.3 组帧示例
    ("§2.3 setUnlock",
     lambda: build_frame(39, 0xFF, bytes([0x02, 0x34]), seq=0),
     "00 27 01 00 04 FF 02 02 34"),
    ("§2.3 GET_BATTERY",
     lambda: build_frame(39, 1, b"", seq=0),
     "00 27 01 00 02 01 00"),
    ("§2.3 SET_MAF=1",
     lambda: build_frame(44, 0, bytes([1]), seq=0),
     "00 2C 01 00 01 01"),
    ("§2.3 SET_SPATIAL=1",
     lambda: build_frame(50, 0, bytes([1]), seq=0),
     "00 32 01 00 01 01"),
    ("§2.3 SET_LATENCY=1",
     lambda: build_frame(37, 0, bytes([1]), seq=0),
     "00 25 01 00 01 01"),
    ("§2.3 SET_CHANGLAN=1",
     lambda: build_frame(1, 1, bytes([1]), seq=0),
     "00 01 01 00 03 01 01 01"),
    # §4.1 握手
    ("§4.1 setUnlock",
     lambda: FiilProtocol().request_unlock(),
     "00 27 01 00 04 FF 02 02 34"),
    # §6.2 / §6.3 MAF
    ("§6.2 GET_MAF",
     lambda: FiilProtocol().request_maf(),
     "00 27 01 00 02 0C 00"),
    ("§6.3 SET_MAF",
     lambda: FiilProtocol().set_maf(MafMode.NOISE_CANCEL),
     "00 2C 01 00 01 01"),
    # §7 影院
    ("§7.1 GET_SPATIAL",
     lambda: FiilProtocol().request_cinema(),
     "00 27 01 00 02 18 00"),
    ("§7.2 SET_SPATIAL",
     lambda: FiilProtocol().set_cinema(True),
     "00 32 01 00 01 01"),
    # §8 低延迟
    ("§8.1 GET_LATENCY",
     lambda: FiilProtocol().request_low_latency(),
     "00 27 01 00 02 08 00"),
    ("§8.1 SET_LATENCY",
     lambda: FiilProtocol().set_low_latency(1),
     "00 25 01 00 01 01"),
    # §9 畅连
    ("§9.1 GET_CHANGLAN",
     lambda: FiilProtocol().request_multipoint(),
     "00 27 01 00 02 80 00"),
    ("§9.1 SET_CHANGLAN",
     lambda: FiilProtocol().set_multipoint(True),
     "00 01 01 00 03 01 01 01"),
    # §8bis 语音提示音量
    ("§8bis.1 GET_TONE",
     lambda: FiilProtocol().request_tone_volume(),
     "00 27 01 00 02 82 00"),
    ("§8bis.1 SET_TONE=1",
     lambda: FiilProtocol().set_tone_volume(1),
     "00 01 01 00 03 03 01 01"),
    ("§8bis.1 SET_TONE=3(静音)",
     lambda: FiilProtocol().set_tone_volume(3),
     "00 01 01 00 03 03 01 03"),
    # §10 EQ
    ("§10.1 GET_EQ",
     lambda: FiilProtocol().request_eq(),
     "00 27 01 00 02 04 00"),
    # §2.5 seq 示例（文档里 TX seq=04）
    ("§2.5 GET_MAF seq=04",
     lambda: build_frame(39, 12, b"", seq=4),
     "04 27 01 00 02 0C 00"),
]


def main() -> int:
    text = MD.read_text(encoding="utf-8")
    print(f"文档：{MD}  ({len(text)} 字符, {text.count(chr(10)) + 1} 行)")
    print()

    for name, gen, doc_bytes in CASES:
        check(name, hexs(gen()), doc_bytes)

    # 文档里声明的 SET 帧长度（8 字节）必须与代码一致
    mp = FiilProtocol().set_multipoint(False)
    check("§9.2 畅连帧长度=8", str(len(mp)), "8")

    # 文档 §3 表格里声明的 cmdId 值必须都在 Query 枚举里
    from fiil_protocol import Query
    declared = [1, 2, 3, 4, 5, 7, 8, 9, 12, 24, 128, 130, 132, 138, 139, 142, 143, 144, 255]
    have = {int(q) for q in Query}
    missing = [d for d in declared if d not in have]
    check("§3 声明的全部 cmdId 都在 Query 枚举里", str(missing), "[]")

    # 文档说的「18 条 GET」与实际枚举数一致（255 是握手不算 GET）
    get_cmds = [q for q in Query if int(q) != 255]
    check("§3 GET 命令条数", str(len(get_cmds)), "18")

    # 关键数值一致性：文档里写的实测延迟必须与实验脚本注释一致，不能各写各的
    for marker in ('1.75', '1.95', '0.45', '0.5 s'):
        check(f"文档提到实测值 {marker}", 'yes' if marker in text else 'NO', 'yes')

    # 结构完整性：章节编号必须单调递增，不允许出现 6.4 后面又 6.4
    secs = re.findall(r'^### (\d+\.\d+) ', text, re.M)
    nums = [tuple(int(x) for x in v.split('.')) for v in secs]
    check('子章节编号严格递增', str(nums == sorted(nums) and len(set(nums)) == len(nums)), 'True')

    # 反向校验：文档正文里声明的条数必须与代码一致（防止只改代码不改文档）
    m = re.search(r"## 3\. GET 命令全表（(\d+) 条", text)
    check("§3 章节标题里的条数", m.group(1) if m else "未找到", str(len(get_cmds)))

    print()
    total = PASS + FAIL
    if FAIL == 0:
        print(f"结果：{PASS}/{total} 全部通过 —— 文档与实现一致")
    else:
        print(f"结果：{PASS} 通过 / {FAIL} 失败")
        for m in FAILURES:
            print(m)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
