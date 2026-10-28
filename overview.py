"""主界面「状态总览」的纯数据模型。

界面只负责把这些行画成卡片；这里不 import tkinter，方便离线测试。
所有文案沿用 FIIL+ 原文（关闭/降噪/开放/兼听/风中），不用外部叫法。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: tone → 语义。界面据此选颜色：ok=已生效，warn=异常/被拒，idle=未连接，off=已关闭。
TONES = ("ok", "warn", "idle", "off")

MAF_LABELS = {0: "关闭", 1: "降噪", 2: "开放", 3: "兼听", 4: "风中"}
LL_LABELS = {0: "关闭", 1: "开启", 2: "音乐", 3: "视频"}
TONE_LABELS = {0: "高", 1: "中", 2: "低", 3: "静音"}


@dataclass(frozen=True)
class OverviewItem:
    """总览里的一格。"""

    key: str
    label: str
    value: str
    tone: str = "idle"

    @property
    def known(self) -> bool:
        return self.tone != "idle"


def _on_off(value: bool | None) -> tuple[str, str]:
    if value is None:
        return "--", "idle"
    return ("开", "ok") if value else ("关", "off")


def battery_items(battery: Any) -> list[OverviewItem]:
    """电量卡片。单电池机型（Key Max 等）只有一个值，不能摊成三格。"""
    if battery is None:
        return [OverviewItem("battery", "电量", "--", "idle")]
    single = getattr(battery, "single", None)
    if single is not None:
        return [OverviewItem("battery", "耳机电量", f"{single}%", "ok")]
    items = []
    for key, label, value in (
        ("battery_left", "左耳", getattr(battery, "left", None)),
        ("battery_right", "右耳", getattr(battery, "right", None)),
        ("battery_case", "充电盒", getattr(battery, "case", None)),
    ):
        text = "--" if value is None else f"{value}%"
        items.append(OverviewItem(key, label, text, "idle" if value is None else "ok"))
    return items


def build_overview(
    *,
    connected: bool,
    maf: int | None = None,
    low_latency: int | None = None,
    cinema: bool | None = None,
    multipoint: bool | None = None,
    tone: int | None = None,
    battery: Any = None,
) -> list[OverviewItem]:
    """把当前已知状态整理成一行行总览卡片。未知一律显示 --，绝不编造默认值。"""
    items = list(battery_items(battery))

    if maf is None:
        items.append(OverviewItem("maf", "降噪模式", "--", "idle"))
    else:
        label = MAF_LABELS.get(int(maf), str(maf))
        items.append(OverviewItem("maf", "降噪模式", label, "off" if int(maf) == 0 else "ok"))

    if low_latency is None:
        items.append(OverviewItem("low_latency", "低延迟", "--", "idle"))
    else:
        label = LL_LABELS.get(int(low_latency), str(low_latency))
        items.append(OverviewItem("low_latency", "低延迟", label,
                                  "off" if int(low_latency) == 0 else "ok"))

    cinema_text, cinema_tone = _on_off(cinema)
    items.append(OverviewItem("cinema", "影院模式", cinema_text, cinema_tone))

    mp_text, mp_tone = _on_off(multipoint)
    items.append(OverviewItem("multipoint", "蓝牙畅连", mp_text, mp_tone))

    if tone is None:
        items.append(OverviewItem("tone", "提示音量", "--", "idle"))
    else:
        items.append(OverviewItem("tone", "提示音量",
                                  TONE_LABELS.get(int(tone), str(tone)), "ok"))

    if not connected:
        # 未连接时读数全部退回未知：避免掉线后界面还挂着上次的数字骗人
        return [OverviewItem(i.key, i.label, "--", "idle") for i in items]
    return items


def connection_text(connected: bool, switching: bool = False) -> tuple[str, str]:
    """顶部状态胶囊的文案与 tone。"""
    if switching:
        return "切换中（畅连主从互换）", "warn"
    return ("已连接", "ok") if connected else ("未连接", "idle")
