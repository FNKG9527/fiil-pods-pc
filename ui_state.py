"""窗口尺寸记忆（与用户设置分开存，避免污染设置表单）。

为什么不塞进 Settings：窗口尺寸是「界面自己记的账」，用户既不会去设置页改它，
也不该在「恢复默认设置」时被一起重置。单独一个文件，坏了大不了回到默认尺寸。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

#: 默认窗口尺寸与最小尺寸
DEFAULT_SIZE = (1280, 820)
MIN_SIZE = (1080, 700)
MAX_SIZE = (6000, 4000)


@dataclass(frozen=True)
class WindowSize:
    width: int = DEFAULT_SIZE[0]
    height: int = DEFAULT_SIZE[1]

    def geometry(self) -> str:
        return f"{self.width}x{self.height}"

    def clamped(self) -> WindowSize:
        w = max(MIN_SIZE[0], min(MAX_SIZE[0], int(self.width)))
        h = max(MIN_SIZE[1], min(MAX_SIZE[1], int(self.height)))
        return WindowSize(w, h)


def parse_geometry(text: str) -> WindowSize | None:
    """把 Tk 的 "1280x820+120+80" 解析成尺寸；格式不对返回 None。"""
    head = str(text or "").strip().split("+", 1)[0].split("-", 1)[0]
    if "x" not in head:
        return None
    left, _, right = head.partition("x")
    try:
        width, height = int(left), int(right)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return WindowSize(width, height).clamped()


def load(path: Path) -> WindowSize:
    """读窗口尺寸。文件缺失或损坏都回到默认值，不抛异常。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return WindowSize()
    if not isinstance(raw, dict):
        return WindowSize()
    try:
        return WindowSize(int(raw.get("width")), int(raw.get("height"))).clamped()
    except (TypeError, ValueError):
        return WindowSize()


def save(path: Path, size: WindowSize) -> None:
    """原子写入窗口尺寸。"""
    size = size.clamped()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps({"width": size.width, "height": size.height}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
