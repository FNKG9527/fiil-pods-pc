"""系统托盘适配层：延迟导入 pystray/Pillow，提供可测试的菜单项模型。

设计约束：
* 模块顶层绝不 import pystray/Pillow，缺依赖时 App 仍能正常启动，只是托盘不可用。
* 菜单项先投影成纯数据（TrayItem），再由 pystray 渲染；这样菜单逻辑可离线测试。
* 所有回调最终都交给 MainThreadDispatcher，绝不在 pystray 线程碰 Tk。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quick_controls import ControlGroup

#: 等待图标线程进入就绪状态的上限。pystray 的 setup 线程超时是 5 秒，这里留余量。
READY_TIMEOUT = 5.0
#: 就绪轮询间隔，避免忙等把启动时间拉长。
READY_POLL_INTERVAL = 0.02


def tolerate_message_filter_failure(module: Any) -> bool:
    """把 ChangeWindowMessageFilterEx 的失败改成可容忍。

    pystray 用它确保任务栏重建消息能被收到，但它的 ``errcheck`` 会把
    ``ERROR_ACCESS_DENIED`` 直接变成异常，图标线程随之崩溃。实测在部分
    Windows 环境下（受限令牌、沙箱、部分企业策略）这个调用必然失败，而收到
    该消息只是"Explorer 重启后图标能自动恢复"的增强项，失败不影响图标创建。
    这里把它包装成：失败即视为成功，始终返回非零。
    """
    target = getattr(module, "ChangeWindowMessageFilterEx", None)
    if target is None or getattr(target, "_fiil_tolerant", False):
        return target is not None

    def tolerant(*args: Any, **_kwargs: Any) -> int:
        try:
            return target(*args) or 1
        except Exception:  # noqa: BLE001 - 失败只降级为"收不到任务栏重建消息"
            return 1

    tolerant._fiil_tolerant = True  # noqa: SLF001 - 给自身打幂等标记
    module.ChangeWindowMessageFilterEx = tolerant
    return True


def install_compat_shims() -> bool:
    """安装运行时兼容补丁。返回 True 表示补丁生效。"""
    if sys.platform != "win32":
        return False
    try:
        from pystray._util import win32 as _win32_util  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - 缺 pystray 时托盘本来就不可用
        return False
    return tolerate_message_filter_failure(_win32_util)



@dataclass(frozen=True)
class TrayBackend:
    """一次依赖探测的结果。available=False 时 error 说明原因，App 降级为普通窗口。"""

    available: bool
    error: str = ""
    pystray: Any = None
    image_module: Any = None


@dataclass(frozen=True)
class TrayItem:
    """菜单项纯数据。action 形如 show/hide/exit/connect/disconnect/refresh/set:<动作>。"""

    label: str
    action: str = ""
    value: object = None
    checked: bool = False
    enabled: bool = True
    radio: bool = False


def load_tray_backend() -> TrayBackend:
    """探测 pystray 与 Pillow；任何缺失都返回不可用而不是抛异常。"""
    try:
        import pystray  # noqa: PLC0415 - 故意延迟导入，缺依赖不能拖垮主程序
    except Exception as exc:  # noqa: BLE001 - 缺依赖/后端异常都要能被 GUI 捕获
        return TrayBackend(False, f"pystray 不可用：{type(exc).__name__}: {exc}")
    try:
        from PIL import Image  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return TrayBackend(False, f"Pillow 不可用：{type(exc).__name__}: {exc}")
    # 已知环境里 ChangeWindowMessageFilterEx 会因权限被拒，必须先让 pystray 容错，
    # 否则图标线程会静默崩溃（run_detached 不报错），托盘等于没启动。
    install_compat_shims()
    return TrayBackend(True, pystray=pystray, image_module=Image)


def tray_icon_path() -> Path:
    """图标查找顺序：冻结包 _MEIPASS → 源码 build/app.ico → 空。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None) or Path(
            getattr(sys, "executable", Path.cwd())
        ).parent
        return Path(base) / "app.ico"
    return Path(__file__).resolve().parent / "build" / "app.ico"


def build_menu_items(groups: Sequence[ControlGroup],
                     connected: bool = True,
                     window_visible: bool = True) -> list[TrayItem]:
    """把菜单模型摊平成托盘菜单项：窗口操作 / 连接操作 / 每个控制组各一段。

    window_visible 决定是否额外提供「隐藏到托盘」；「显示主窗口」始终存在并作为
    托盘双击的默认动作，保证双击任何状态下都是把窗口唤到前台。
    """
    del connected  # 连接态已体现在每个 ControlOption.enabled 上
    window_items = [TrayItem("显示主窗口", "show")]
    if window_visible:
        window_items.append(TrayItem("隐藏到托盘", "hide"))
    items = [
        *window_items,
        TrayItem("退出程序", "exit"),
        TrayItem("", "separator"),
        TrayItem("连接设备", "connect", enabled=True),
        TrayItem("断开连接", "disconnect", enabled=True),
        TrayItem("刷新状态", "refresh", enabled=True),
    ]
    for group in groups:
        items.append(TrayItem("", "separator"))
        for option in group.options:
            items.append(
                TrayItem(
                    f"{group.label} · {option.label}",
                    f"set:{option.action}",
                    option.value,
                    checked=option.checked,
                    enabled=option.enabled,
                    radio=len(group.options) > 2,
                )
            )
    return items


class TrayIcon:
    """包装 pystray.Icon，只暴露 start/stop/update 三个动作给 App。"""

    def __init__(self, backend: TrayBackend, title: str,
                 on_action: Callable[[TrayItem], None],
                 icon_path: Path | None = None,
                 ready_timeout: float = READY_TIMEOUT,
                 poll_interval: float = READY_POLL_INTERVAL) -> None:
        self.backend = backend
        self.title = title
        self.on_action = on_action
        self.icon_path = icon_path or tray_icon_path()
        self.ready_timeout = ready_timeout
        self.poll_interval = poll_interval
        self._icon: Any = None
        self._items: list[TrayItem] = []
        self.error = ""

    @property
    def available(self) -> bool:
        return self._icon is not None

    def start(self, items: Sequence[TrayItem]) -> bool:
        if not self.backend.available:
            self.error = self.backend.error or "托盘依赖不可用"
            return False
        if self._icon is not None:
            return True
        self._items = list(items)
        self.error = ""
        try:
            image = self.backend.image_module.open(self.icon_path)
            icon = self.backend.pystray.Icon("FiilPodsPC", image, self.title, self._build_menu())
            icon.run_detached()
        except Exception as exc:  # noqa: BLE001 - 通知区不可用不是致命错误
            self.error = f"托盘启动失败：{type(exc).__name__}: {exc}"
            return False
        # run_detached 立即返回，真正的创建发生在内部线程；线程崩溃不会冒泡到这里，
        # 必须轮询就绪状态，否则会把"托盘根本不存在"误判成可用。
        if not self._wait_until_ready(icon):
            self.error = "托盘图标未能创建（通知区不可用或图标线程异常退出）"
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass
            return False
        self._icon = icon
        return True

    def _wait_until_ready(self, icon: Any) -> bool:
        deadline = time.monotonic() + self.ready_timeout
        while True:
            if getattr(icon, "_running", None) and getattr(icon, "_visible", None):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.poll_interval)

    def update(self, items: Sequence[TrayItem]) -> None:
        self._items = list(items)
        if self._icon is None:
            return
        try:
            self._icon.menu = self._build_menu()
            self._icon.update_menu()
        except Exception:  # noqa: BLE001 - 刷新失败保留旧菜单总比崩溃好
            pass

    def stop(self) -> None:
        icon, self._icon = self._icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass

    def _build_menu(self) -> Any:
        Menu, MenuItem = self.backend.pystray.Menu, self.backend.pystray.MenuItem
        separator = getattr(Menu, "SEPARATOR", None)
        menu_items = []
        for item in self._items:
            if item.action == "separator":
                if separator is not None:
                    menu_items.append(separator)
                continue
            if item.action == "show":
                menu_items.append(MenuItem(item.label, self._activate(item), default=True))
            elif item.action in ("hide", "connect", "disconnect", "refresh", "exit"):
                menu_items.append(MenuItem(item.label, self._activate(item),
                                           enabled=item.enabled))
            else:
                checked = (lambda _item, flag=item.checked: flag) if item.radio else None
                menu_items.append(
                    MenuItem(
                        item.label,
                        self._activate(item),
                        checked=checked,
                        radio=item.radio,
                        enabled=item.enabled,
                    )
                )
        # 控制组之间用 Menu.SEPARATOR 划段（TrayItem.action == "separator"）
        return Menu(*menu_items)

    def _activate(self, item: TrayItem) -> Callable[[Any, Any], None]:
        def handler(_icon: Any = None, _item: Any = None) -> None:
            self.on_action(item)

        return handler
