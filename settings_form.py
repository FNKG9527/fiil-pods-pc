"""设置页表单的纯数据描述（不 import tkinter）。

界面按这里的描述生成控件，测试按这里的描述检查「模型里每个字段都有对应的
表单项」—— 以后给 Settings 加字段却忘了做界面，测试会直接失败。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import settings as st

#: 控件种类：开关 / 数值 / 下拉
BOOL = "bool"
NUMBER = "number"


@dataclass(frozen=True)
class FormRow:
    """一行设置项。"""

    key: str
    label: str
    kind: str
    hint: str = ""
    unit: str = ""
    #: 数值型的小数位数（0 = 整数）
    decimals: int = 0

    def bounds(self) -> tuple[float, float] | None:
        return st.field_rules()[self.key][1]

    def to_widget(self, value: Any) -> str:
        """设置值 → 控件里显示的文本。"""
        if self.kind == BOOL:
            return "1" if value else "0"
        if self.decimals:
            return f"{float(value):.{self.decimals}f}"
        return str(int(value))

    def from_widget(self, raw: Any) -> Any:
        """控件文本 → 设置值。非法输入抛 ValueError，由调用方收集成告警。"""
        if self.kind == BOOL:
            return st._as_bool(raw)  # noqa: SLF001 - 同一模块族的内部约定
        text = str(raw).strip()
        if not text:
            raise ValueError("不能为空")
        return float(text) if self.decimals else int(float(text))


@dataclass(frozen=True)
class FormSection:
    title: str
    note: str
    rows: tuple[FormRow, ...]


SECTIONS: tuple[FormSection, ...] = (
    FormSection(
        title="连接",
        note="连接策略会按「缓存直连 → 地址直连 → 边扫边连 → 地址兜底」依次尝试，"
             "耳机不广播时不必再等满扫描超时。",
        rows=(
            FormRow("auto_connect", "启动时自动连接上次的设备", BOOL,
                    hint="记住上次连过的地址，启动后自动尝试连接"),
            FormRow("scan_timeout", "扫描时长", NUMBER, unit="秒", decimals=1,
                    hint="越长越容易扫到远处的设备，但点「扫描」后要等更久"),
            FormRow("connect_timeout", "连接超时", NUMBER, unit="秒",
                    hint="单次建立连接的上限；超时后自动尝试下一种连接方式"),
        ),
    ),
    FormSection(
        title="界面",
        note="主窗口只保留控制与状态总览，收发日志全部收进本设置页的「日志」标签。",
        rows=(
            FormRow("show_status_overview", "主窗口显示状态总览", BOOL,
                    hint="右侧卡片式显示电量、降噪、低延迟、影院等当前状态"),
            FormRow("autoscroll", "日志自动滚动到最新", BOOL),
            FormRow("log_font_size", "日志字号", NUMBER, unit="pt",
                    hint="等宽字体，调大可减少长时间盯屏的疲劳"),
            FormRow("log_max_lines", "日志最多保留", NUMBER, unit="行",
                    hint="超出后丢弃最旧的记录，避免长时间挂机吃满内存"),
            FormRow("remember_window", "记住窗口大小", BOOL,
                    hint="下次启动恢复上次关闭时的窗口尺寸"),
        ),
    ),
    FormSection(
        title="行为",
        note="",
        rows=(
            FormRow("auto_hide_to_tray", "启动后直接隐藏到托盘", BOOL,
                    hint="适合开机自启：启动即在后台待命，右键托盘图标即可唤出"),
            FormRow("confirm_before_exit", "退出前确认", BOOL,
                    hint="防止误点「退出程序」把正在用的连接关掉"),
        ),
    ),
)


def all_rows() -> tuple[FormRow, ...]:
    return tuple(row for section in SECTIONS for row in section.rows)


def to_values(current: st.Settings) -> dict[str, str]:
    """设置对象 → 控件初值。"""
    return {row.key: row.to_widget(getattr(current, row.key)) for row in all_rows()}


def from_values(current: st.Settings, values: dict[str, Any]) -> tuple[st.Settings, list[str]]:
    """控件值 → 设置对象。非法项保留原值并给出告警，绝不整份丢弃。"""
    changes: dict[str, Any] = {}
    warnings: list[str] = []
    for row in all_rows():
        if row.key not in values:
            continue
        try:
            cooked = row.from_widget(values[row.key])
        except (TypeError, ValueError) as exc:
            warnings.append(f"「{row.label}」输入无效（{exc}），保留原值")
            continue
        bounds = row.bounds()
        if bounds is not None:
            low, high = bounds
            if not (low <= cooked <= high):
                clamped = low if cooked < low else high
                warnings.append(f"「{row.label}」超出 {low}~{high}，已改为 {clamped}")
                cooked = clamped
        changes[row.key] = cooked
    return current.with_changes(**changes), warnings


def describe(settings_obj: st.Settings) -> str:
    """把设置渲染成一行行「标签：值」，用于「关于」页与日志里的变更记录。"""
    lines = []
    for section in SECTIONS:
        lines.append(f"[{section.title}]")
        for row in section.rows:
            value = getattr(settings_obj, row.key)
            if row.kind == BOOL:
                shown = "开" if value else "关"
            elif row.decimals:
                shown = f"{float(value):.{row.decimals}f}{row.unit}"
            else:
                shown = f"{int(value)}{row.unit}"
            lines.append(f"  {row.label}：{shown}")
    return "\n".join(lines)
