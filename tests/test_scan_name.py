# -*- coding: utf-8 -*-
"""扫描名称解析专项测试（不连真机也能跑的部分）。

背景：FIIL 耳机是双模设备，BLE 广播通常不带 local_name，
且 BLE 广播地址与经典蓝牙 BD_ADDR 只差最后一个字节。
`ble_worker._scan()` 依赖 `_match_nearby_cached_name()` 做前缀回退，
这里把该函数的边界行为钉死，防止后续改动退化成「又只剩 MAC」。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ble_worker import FIIL_OUI_PREFIXES, _match_nearby_cached_name  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  OK   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n       期望 {want!r}\n       实际 {got!r}")


def main() -> int:
    print("== 真实场景：本机 FIIL Key Max ==")
    cached = {
        "B0:F1:A3:00:00:02": "FIIL Key Max",   # 经典蓝牙地址
        "AA:BB:CC:DD:EE:FF": "OPPO Enco Free4",
    }
    # BLE 广播地址，最后一字节 9D -> C8
    check("前缀回退命中真名",
          _match_nearby_cached_name("B0:F1:A3:00:00:01", cached), "FIIL Key Max")

    print("\n== 恰好同地址也必须命中 ==")
    check("地址完全相同",
          _match_nearby_cached_name("B0:F1:A3:00:00:02", cached), "FIIL Key Max")

    print("\n== 不能张冠李戴 ==")
    # 同一 OUI + 同前 5 段但缓存里有两条 -> 无法判定，返回空
    dup = {
        "B0:F1:A3:00:00:02": "FIIL Key Max",
        "B0:F1:A3:00:00:11": "FIIL CC Pro3",
    }
    check("同前缀两条缓存 -> 不猜",
          _match_nearby_cached_name("B0:F1:A3:00:00:01", dup), "")

    # 前缀不同（第 5 段不同）-> 不能命中
    check("第 5 段不同 -> 不命中",
          _match_nearby_cached_name("B0:F1:A3:11:22:33", cached), "")

    # 完全不同的设备
    check("无关设备 -> 不命中",
          _match_nearby_cached_name("11:22:33:44:55:66", cached), "")

    print("\n== 空缓存 ==")
    check("空表返回空串", _match_nearby_cached_name("B0:F1:A3:00:00:01", {}), "")

    print("\n== OUI 常量 ==")
    check("OUI 含 B0:F1:A3", "B0:F1:A3" in FIIL_OUI_PREFIXES, True)
    check("OUI 含 FF:F1:A3", "FF:F1:A3" in FIIL_OUI_PREFIXES, True)

    print("\n== 大小写不敏感 ==")
    check("小写地址也能匹配",
          _match_nearby_cached_name("b0:f1:a3:00:00:01", cached), "FIIL Key Max")

    print("\n== 已知设备表（耳机不广播时的兜底） ==")
    import pathlib

    import ble_worker as bw
    p = pathlib.Path(bw._KNOWN_DEVICES_PATH)
    bak = p.read_text(encoding="utf-8") if p.exists() else None
    try:
        if p.exists():
            p.unlink()
        bw._save_known_device("b0:f1:a3:00:00:01", "FIIL Key Max")
        t = bw._load_known_devices()
        check("保存后能读回", t.get("B0:F1:A3:00:00:01", {}).get("name"), "FIIL Key Max")
        # 连接成功先写空名、读到型号再补真名；空写不能把真名抹掉
        bw._save_known_device("B0:F1:A3:00:00:01", "")
        t = bw._load_known_devices()
        check("空名写入不抹掉已有型号",
              t.get("B0:F1:A3:00:00:01", {}).get("name"), "FIIL Key Max")
        p.write_text("not json", encoding="utf-8")
        check("坏文件返回空表", bw._load_known_devices(), {})
    finally:
        p.write_text(bak, encoding="utf-8") if bak is not None else (p.exists() and p.unlink())

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


def test_all() -> None:
    """pytest 入口。"""
    main()
    assert FAIL == 0, f"{FAIL} 项失败"


if __name__ == "__main__":
    sys.exit(main())
