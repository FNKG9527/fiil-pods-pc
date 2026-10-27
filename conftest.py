# -*- coding: utf-8 -*-
"""pytest 引导：让 tests/ 下的用例能 import 仓库根目录的核心模块。

仓库是「根目录平铺核心模块 + tests/ 放离线测试」的结构，没有打包成
package，所以需要把仓库根显式加进 sys.path。单独运行某个测试脚本时
（python tests/test_x.py）各脚本自己也会插入一次。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
