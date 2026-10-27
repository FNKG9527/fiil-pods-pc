"""托盘依赖探测与菜单构建的离线测试（不创建真实通知区图标）。"""

from __future__ import annotations

import builtins
import re
from pathlib import Path
from types import SimpleNamespace

from quick_controls import ControlState, menu_model
from tray_support import TrayIcon, build_menu_items, load_tray_backend, tray_icon_path


def test_missing_pystray_returns_unavailable_backend(monkeypatch):
    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "pystray":
            raise ImportError("simulated missing dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    backend = load_tray_backend()
    assert backend.available is False
    assert "pystray" in backend.error


def test_missing_pillow_returns_unavailable_backend(monkeypatch):
    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.split(".")[0] == "PIL":
            raise ImportError("simulated missing Pillow")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    backend = load_tray_backend()
    assert backend.available is False


def test_menu_items_mirror_groups_and_dispatch_values():
    groups = menu_model(ControlState(connected=True, maf=1, low_latency=1,
                                     cinema=False, multipoint=True, tone=3))
    items = build_menu_items(groups)

    actions = [item.action for item in items]
    # 「显示主窗口」常驻（托盘双击的默认动作），窗口可见时再多一项「隐藏到托盘」
    assert actions[:7] == ["show", "hide", "exit", "separator", "connect", "disconnect", "refresh"]
    controls = [item for item in items if item.action.startswith("set:")]
    assert controls
    maf_on = next(i for i in controls if i.action == "set:maf" and int(i.value) == 1)
    assert maf_on.checked and maf_on.enabled
    cinema_off = next(i for i in controls if i.action == "set:cinema" and i.value is False)
    assert cinema_off.checked
    # 未连接时菜单项存在但不可点
    disabled = build_menu_items(menu_model(ControlState(connected=False)))
    assert all(not item.enabled for item in disabled if item.action.startswith("set:"))


def test_menu_sections_are_separated():
    groups = menu_model(ControlState(connected=True, maf=1, low_latency=1))
    items = build_menu_items(groups)

    separators = [i for i, item in enumerate(items) if item.action == "separator"]
    # 连接操作前 1 条 + 五个控制组各 1 条
    assert len(separators) == 6

    def section_of(index: int) -> int:
        return sum(1 for s in separators if s < index)

    assert section_of(items.index(next(i for i in items if i.action == "hide"))) == 0
    assert section_of(items.index(next(i for i in items if i.action == "connect"))) == 1
    first_control = items.index(next(i for i in items if i.action.startswith("set:")))
    assert section_of(first_control) == 2
    # 分隔线本身不参与点击
    assert all(item.label == "" for item in items if item.action == "separator")


def test_window_action_follows_visibility_state():
    visible_items = build_menu_items(menu_model(ControlState(connected=True)), window_visible=True)
    hidden_items = build_menu_items(menu_model(ControlState(connected=True)), window_visible=False)

    # 「显示主窗口」始终在，保证双击托盘任何状态都能唤出窗口
    assert visible_items[0].action == "show"
    assert hidden_items[0].action == "show"
    # 窗口已可见时再多一个「隐藏到托盘」；已隐藏时不再提供它
    assert [i.action for i in visible_items[:2]] == ["show", "hide"]
    assert visible_items[0].label == "显示主窗口"
    assert visible_items[0].enabled
    assert hidden_items[0].label == "显示主窗口"
    assert hidden_items[0].enabled
    assert not any(i.action == "hide" for i in hidden_items)


def test_tray_icon_path_prefers_bundled_asset(monkeypatch, tmp_path):
    monkeypatch.setattr("tray_support.sys", SimpleNamespace(frozen=True, _MEIPASS=str(tmp_path)))
    assert tray_icon_path() == tmp_path / "app.ico"


class _FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items):
        self.items = items


class _FakeMenuItem:
    def __init__(self, text, action, **kwargs):
        self.text = text
        self.action = action
        self.options = kwargs


class _FakeModule:
    # 这些假对象只在内存中模拟消息过滤器调用失败
    def __init__(self, exc):
        self.ChangeWindowMessageFilterEx = self._call(exc)
        self.calls = 0

    def _call(self, exc):
        def call(*args):
            self.calls += 1
            raise exc

        return call


class _FakeImage:
    @staticmethod
    def open(_path):
        return object()


def _backend(icon_factory):
    return SimpleNamespace(
        available=True,
        error="",
        image_module=_FakeImage,
        pystray=SimpleNamespace(
            Icon=icon_factory,
            Menu=_FakeMenu,
            MenuItem=_FakeMenuItem,
        ),
    )


class _DeadIcon:
    """模拟图标线程启动即崩溃：run_detached 不报错，但永远不进入就绪状态。"""

    instances = []

    def __init__(self, name, image, title, menu):
        self.menu = menu
        self._running = False
        self._visible = False
        self.stopped = False
        _DeadIcon.instances.append(self)

    def run_detached(self):
        return None

    def stop(self):
        self.stopped = True

    def update_menu(self):
        return None


class _ReadyIcon(_DeadIcon):
    def run_detached(self):
        self._running = True
        self._visible = True


class _RaisingIcon(_DeadIcon):
    def run_detached(self):
        raise RuntimeError("通知区不可用")


def _tray(icon_factory, **kwargs):
    defaults = {"ready_timeout": 0.2, "poll_interval": 0.01}
    defaults.update(kwargs)
    return TrayIcon(_backend(icon_factory), "标题", lambda item: None, **defaults)


def test_start_fails_when_icon_thread_never_becomes_ready():
    _DeadIcon.instances.clear()
    tray = _tray(_DeadIcon)

    # 图标线程崩溃后 run_detached 不会报错，必须由就绪校验兜住
    assert tray.start([]) is False
    assert tray.available is False
    assert tray.error
    assert _DeadIcon.instances and _DeadIcon.instances[0].stopped is True


def test_start_succeeds_once_icon_reports_ready():
    tray = _tray(_ReadyIcon)
    assert tray.start([]) is True
    assert tray.available is True
    assert tray.error == ""


def test_start_fails_when_run_detached_raises():
    tray = _tray(_RaisingIcon)
    assert tray.start([]) is False
    assert tray.available is False
    assert tray.error


def test_message_filter_denial_is_tolerated():
    import tray_support

    module = _FakeModule(PermissionError(13, "拒绝访问。", None, 5))
    assert tray_support.tolerate_message_filter_failure(module) is True

    # 补丁后调用不再抛异常，返回非零表示成功继续
    assert module.ChangeWindowMessageFilterEx(1, 2, 3, None) == 1
    # 幂等：重复安装不会二次包装
    assert tray_support.tolerate_message_filter_failure(module) is True
    assert module.ChangeWindowMessageFilterEx(1, 2, 3, None) == 1


def test_message_filter_shim_ignores_non_windows_platforms():
    import tray_support

    assert tray_support.install_compat_shims() in (True, False)


def test_declared_runtime_dependencies_cover_tray():
    text = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(encoding="utf-8")
    names = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        names.append(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower().replace("_", "-"))
    assert "pystray" in names
    assert "pillow" in names
