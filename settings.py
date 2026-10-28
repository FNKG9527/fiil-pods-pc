"""用户设置的纯数据模型与持久化（不依赖 tkinter / BLE）。

设计约束：
* 设置文件损坏、字段缺失、类型不对都**不能**让程序起不来 —— 一律回落到默认值，
  并把「哪一项被修正」以告警列表返回给界面显示，而不是静默吞掉。
* 写入必须原子：先写同目录临时文件再 ``os.replace``，避免断电/崩溃留下半个 JSON。
* 这里不 import tkinter，保证可以在无图形环境（CI）里直接测试。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

#: 设置文件里允许出现的键 → 类型转换器。未知键一律丢弃（向前兼容旧文件）。
_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "是", "开"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", "否", "关"})


@dataclass(frozen=True)
class Settings:
    """程序设置。全部有默认值，``Settings()`` 就是出厂状态。"""

    # 连接
    auto_connect: bool = True          # 启动时用上次地址自动连接
    scan_timeout: float = 8.0          # 扫描时长（秒）
    connect_timeout: float = 20.0      # 单次连接超时（秒）
    # 界面
    autoscroll: bool = True            # 日志自动滚动
    log_font_size: int = 9             # 日志字号
    log_max_lines: int = 4000          # 日志最多保留行数，超出丢弃最旧的
    show_status_overview: bool = True  # 主窗口右侧显示状态总览
    # 行为
    auto_hide_to_tray: bool = False    # 启动后直接隐藏到托盘
    confirm_before_exit: bool = True   # 退出前确认
    remember_window: bool = True       # 记住窗口大小

    def with_changes(self, **changes: Any) -> Settings:
        """返回替换若干字段后的新实例（frozen dataclass 的惯用写法）。"""
        return replace(self, **changes)


#: 字段名 → (转换函数, 允许区间或 None)。区间只对数值生效。
_FIELD_RULES: dict[str, tuple[Any, tuple[float, float] | None]] = {
    "auto_connect": (bool, None),
    "scan_timeout": (float, (2.0, 60.0)),
    "connect_timeout": (float, (5.0, 60.0)),
    "autoscroll": (bool, None),
    "log_font_size": (int, (7, 20)),
    "log_max_lines": (int, (200, 100_000)),
    "show_status_overview": (bool, None),
    "auto_hide_to_tray": (bool, None),
    "confirm_before_exit": (bool, None),
    "remember_window": (bool, None),
}

KNOWN_KEYS = frozenset(_FIELD_RULES)
DEFAULTS = Settings()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    raise ValueError(f"无法当作布尔值：{value!r}")


def _as_number(value: Any, caster: Any) -> float | int:
    if isinstance(value, bool):  # bool 是 int 子类，必须先挡掉，否则 True→1
        raise ValueError(f"无法当作数字：{value!r}")
    return caster(value)


def coerce(raw: Any) -> tuple[Settings, list[str]]:
    """把任意来源的字典整理成合法 Settings，并返回被修正项的说明。"""
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return DEFAULTS, ["设置内容不是对象，已使用默认设置"]

    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in _FIELD_RULES:
            warnings.append(f"忽略未知设置项 {key}")
            continue
        caster, bounds = _FIELD_RULES[key]
        try:
            cooked = _as_bool(value) if caster is bool else _as_number(value, caster)
        except (TypeError, ValueError):
            warnings.append(f"设置项 {key}={value!r} 无效，已使用默认值")
            continue
        if bounds is not None:
            low, high = bounds
            if not (low <= cooked <= high):
                clamped = low if cooked < low else high
                warnings.append(f"设置项 {key}={cooked} 超出范围 {low}~{high}，已改为 {clamped}")
                cooked = clamped
        values[key] = cooked
    return Settings(**values), warnings


def load(path: Path) -> tuple[Settings, list[str]]:
    """读取设置文件。文件不存在 → 默认值且不报错；文件坏了 → 默认值 + 告警。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DEFAULTS, []
    except OSError as exc:
        return DEFAULTS, [f"设置文件无法读取（{exc}），已使用默认设置"]
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return DEFAULTS, [f"设置文件格式损坏（{exc.msg}），已使用默认设置"]
    return coerce(raw)


def save(path: Path, settings: Settings) -> None:
    """原子写入设置文件；目录不存在时自动创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(settings), ensure_ascii=False, indent=2, sort_keys=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.replace(tmp, path)


def field_names() -> tuple[str, ...]:
    """当前模型里的全部字段名（界面生成表单时用，避免手写两份列表）。"""
    return tuple(f.name for f in fields(Settings))


def field_rules() -> dict[str, tuple[Any, tuple[float, float] | None]]:
    """字段 → (转换类型, 允许区间)。界面据此决定控件类型与 Spinbox 范围。"""
    return dict(_FIELD_RULES)
