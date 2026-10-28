"""快捷切换控制目录与只读菜单模型（无 GUI、无 BLE 依赖）。

主窗口按钮与系统托盘菜单都从这里取「有哪些控制项、当前是哪个、是否可点」，
避免两套界面各写一份文案后逐渐漂移。
"""

from __future__ import annotations

from dataclasses import dataclass

from fiil_protocol import LowLatencyMode, MafMode, ToneVolume


@dataclass(frozen=True)
class ControlState:
    """界面已知的耳机状态快照。None 表示还没读到，不要假设默认值。"""

    connected: bool
    maf: int | None = None
    low_latency: int | None = None
    cinema: bool | None = None
    multipoint: bool | None = None
    tone: int | None = None
    #: LDAC 高清解码开关；None = 未读到
    ldac: bool | None = None
    #: LDAC 是否已在 App 里激活（走 FIIL 服务器，PC 端只能读不能改）
    ldac_unlocked: bool | None = None


@dataclass(frozen=True)
class ControlOption:
    action: str
    label: str
    value: object
    checked: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class ControlGroup:
    action: str
    label: str
    options: tuple[ControlOption, ...]


def _enum_options(action: str, enum_type) -> tuple[ControlOption, ...]:
    return tuple(ControlOption(action, item.label_cn, item) for item in enum_type)


def _bool_options(action: str) -> tuple[ControlOption, ...]:
    return (
        ControlOption(action, "关", False),
        ControlOption(action, "开", True),
    )


CONTROL_GROUPS: tuple[ControlGroup, ...] = (
    ControlGroup("maf", "降噪模式 MAF", _enum_options("maf", MafMode)),
    ControlGroup("low_latency", "低延迟模式", _enum_options("low_latency", LowLatencyMode)),
    ControlGroup("cinema", "影院模式", _bool_options("cinema")),
    ControlGroup("multipoint", "蓝牙畅连", _bool_options("multipoint")),
    ControlGroup("tone", "语音提示音量", _enum_options("tone", ToneVolume)),
    ControlGroup("ldac", "LDAC 高清解码", _bool_options("ldac")),
)

# 连接到耳机才有意义的操作
CONTROL_ACTIONS = frozenset(group.action for group in CONTROL_GROUPS)


def menu_model(state: ControlState) -> tuple[ControlGroup, ...]:
    """把状态快照投影成菜单项。

    Mate 机型只支持低延迟 0/1；只有读到 2/3 才说明是 GAIA 机型，
    此时才生成「音乐 / 视频」档，避免给用户一个必然被拒绝的选项。
    """
    groups: list[ControlGroup] = []
    for group in CONTROL_GROUPS:
        current = getattr(state, group.action)
        label = group.label
        # LDAC 需要先在官方 App 里激活（激活码由 FIIL 服务器下发，PC 端离线做不到），
        # 未激活时把档位禁用并把原因写进分组标题，避免用户点了没反应以为是 bug。
        locked = group.action == "ldac" and state.ldac_unlocked is not True
        if locked:
            label = f"{group.label}（需先在手机 App 激活）"
        options: list[ControlOption] = []
        for option in group.options:
            value = int(option.value) if isinstance(option.value, int) and not isinstance(
                option.value, bool) else option.value
            if (group.action == "low_latency" and state.low_latency in (0, 1)
                    and value in (int(LowLatencyMode.MUSIC), int(LowLatencyMode.VIDEO))):
                continue
            options.append(
                ControlOption(
                    option.action,
                    option.label,
                    value,
                    checked=current == value,
                    enabled=state.connected and not locked,
                )
            )
        groups.append(ControlGroup(group.action, label, tuple(options)))
    return tuple(groups)
