# -*- coding: utf-8 -*-
"""电量解析健壮性验证：空帧 / 短帧 / 异常值都不得崩。

两种跑法都行：
    python tests/test_battery_robust.py
    pytest tests/test_battery_robust.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fiil_protocol import BatteryState  # noqa: E402

CASES = [
    (b"", "空帧"),
    (b"\x50", "1 字节"),
    (b"\x00\x50", "2 字节"),
    (b"\x00\x50\x00", "3 字节（正常）"),
    (b"\xff\xff\xff", "全 0xFF（无效值）"),
    (b"\x00\x00\x00", "全 0（无效值）"),
    (b"\x64\x32\x0a", "100/50/10"),
    (b"\x00\x4d\x00", "实测样本 左0 右77 盒0"),
    (bytes(range(20)), "超长 20 字节"),
]


def run(verbose: bool = True) -> list[str]:
    """跑全部用例，返回失败说明列表（空列表 = 全过）。"""
    fails: list[str] = []
    for raw, desc in CASES:
        try:
            b = BatteryState.parse(raw)
            disp = b.display()
            if verbose:
                print(f"{desc:<22} {raw.hex(' ') or '(empty)':<20} -> "
                      f"l={b.left} r={b.right} c={b.case}  {disp}")
            # 不变量：任何解析结果都必须在 0..100 之间或是 None
            for v in (b.left, b.right, b.case):
                if v is not None and not (0 <= v <= 100):
                    fails.append(f"{desc}: 越界值 {v}")
        except Exception as e:  # noqa: BLE001
            fails.append(f"{desc}: {type(e).__name__}: {e}")
            if verbose:
                print(f"{desc:<22} {raw.hex(' ') or '(empty)':<20} -> "
                      f"EXCEPTION {type(e).__name__}: {e}")
    return fails


def test_all() -> None:
    """pytest 入口。"""
    fails = run(verbose=False)
    assert not fails, "电量解析健壮性失败：\n" + "\n".join(fails)


if __name__ == "__main__":
    fails = run()
    print()
    print(f"结果：{len(CASES) - len(fails)}/{len(CASES)} 正常，{len(fails)} 个异常")
    sys.exit(0 if not fails else 1)
