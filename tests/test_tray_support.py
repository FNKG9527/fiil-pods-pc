"""托盘依赖探测与菜单构建的离线测试（不创建真实通知区图标）。"""

from __future__ import annotations

import builtins
import re
from pathlib import Path
from types import SimpleNamespace

from quick_controls import ControlState, menu_model
from tray_support import build_menu_items, load_tray_backend, tray_icon_path


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
