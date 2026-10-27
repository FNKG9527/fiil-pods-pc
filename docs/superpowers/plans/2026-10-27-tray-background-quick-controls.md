# FiilPods PC 系统托盘后台与快捷切换实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Windows 控制台关闭窗口后仍驻留系统托盘，双击恢复，右键安全切换全部已验证耳机模式。

**Architecture:** `quick_controls.py` 提供无 GUI 的控制目录和只读菜单模型；`tray_support.py` 隔离 `pystray` 可选依赖；`app.py` 的薄协调器只在 Tk 主线程运行窗口动作，把命令交给既有 `BleWorker`，沿用原 ACK 与读回校验。

**Tech Stack:** Python 3.10+ / Tkinter / `pystray` + Pillow（Windows 托盘）/ pytest / PyInstaller / Inno Setup。

## Global Constraints

- 遵守 `docs/superpowers/specs/2026-10-27-tray-background-quick-controls-design.md` 的完整托盘方案；不加入全局热键、开机自启、自动连接或独立服务。
- 所有 Tk 控件读取和修改仅在 Tk 主线程；托盘线程只提交回调。
- 不修改协议命令、不重写 BLE 状态机；托盘动作复用 App 原动作。
- 新逻辑必须有先失败、后通过的离线测试；GUI/真机未执行项目必须明确标记。
- 保留 GPL-3.0 与仓库文档规范，不提交真实设备 MAC、本机路径或 `known_devices.json`。
- 开发命令统一从仓库根 `D:\DeepSeek\fiil_pods_pc` 执行；Windows Python 为 `D:\python\python.exe`，其他环境使用 `python`。

## File Structure

- 新建 `quick_controls.py`：控制项目录、`ControlState` 数据快照、`menu_model()`。
- 新建 `tray_support.py`：可选依赖检测、pystray 图标创建、菜单回调适配。
- 新建 `window_lifecycle.py`：与 Tk/BLE 解耦的窗口和托盘生命周期协调器。
- 新建 `tests/test_quick_controls.py`、`tests/test_tray_support.py`、`tests/test_window_lifecycle.py`、`tests/test_tray_actions.py`。
- 修改 `app.py`：共享目录驱动按钮、状态快照、主线程调度、托盘启动与窗口回调。
- 修改 `requirements.txt`、`build/fiil_pods.spec`：运行依赖与打包收集。
- 修改 `README.md`、`CHANGELOG.md`、`build/使用说明.txt`、`build/version_info.txt`、`build/installer.iss`：使用说明与版本。

---

### Task 1: 共享控制目录与只读菜单模型

**Files:**
- Create: `quick_controls.py`
- Test: `tests/test_quick_controls.py`

**Interfaces:**
- Consumes: `fiil_protocol.MafMode`、`LowLatencyMode`、`ToneVolume`。
- Produces: `ControlState(connected: bool, maf: int|None, low_latency: int|None, cinema: bool|None, multipoint: bool|None, tone: int|None)`；`ControlOption(action: str, label: str, value: object, checked: bool, enabled: bool)`；`ControlGroup(action: str, label: str, options: tuple[ControlOption, ...])`；`menu_model(state: ControlState) -> tuple[ControlGroup, ...]`。

- [ ] **Step 1: 写目录与状态失败测试**

```python
from fiil_protocol import LowLatencyMode, MafMode, ToneVolume
from quick_controls import ControlState, CONTROL_GROUPS, menu_model


def test_catalog_uses_verified_protocol_values_and_unique_actions():
    actions = [option.action for group in CONTROL_GROUPS for option in group.options]
    assert len(actions) == len(set(actions))
    assert [o.value for o in CONTROL_GROUPS[0].options] == list(MafMode)
    assert [o.value for o in CONTROL_GROUPS[3].options] == list(ToneVolume)


def test_disconnected_snapshot_disables_every_control():
    groups = menu_model(ControlState(connected=False))
    assert all(not option.enabled for group in groups for option in group.options)


def test_menu_model_checks_modes_and_hides_gaia_levels_until_supported():
    groups = menu_model(ControlState(connected=True, maf=1, low_latency=1,
                                     cinema=True, multipoint=False, tone=2))
    maf, latency, cinema, multipoint, tone = groups
    assert [o.checked for o in maf.options] == [False, True, False, False, False]
    assert [o.value for o in latency.options] == [0, 1]
    assert latency.options[1].checked
    assert cinema.options[1].checked and not multipoint.options[1].checked
    assert tone.options[2].checked
    gaia = menu_model(ControlState(connected=True, low_latency=2))[1]
    assert [o.value for o in gaia.options] == list(LowLatencyMode)
    assert gaia.options[2].checked


def test_boolean_menu_uses_explicit_on_off_targets_not_blind_toggle():
    groups = menu_model(ControlState(connected=True, cinema=False))
    assert [(o.action, o.value) for o in groups[2].options] == [("cinema", False), ("cinema", True)]
```

- [ ] **Step 2: 运行测试，确认因模块不存在失败**

Run: `python -m pytest tests/test_quick_controls.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'quick_controls'`。

- [ ] **Step 3: 实现最小共享模型**

```python
from dataclasses import dataclass
from fiil_protocol import LowLatencyMode, MafMode, ToneVolume


@dataclass(frozen=True)
class ControlState:
    connected: bool
    maf: int | None = None
    low_latency: int | None = None
    cinema: bool | None = None
    multipoint: bool | None = None
    tone: int | None = None


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


MAF = tuple((m, m.label_cn) for m in MafMode)
LATENCY = tuple((m, m.label_cn) for m in LowLatencyMode)
TONE = tuple((m, m.label_cn) for m in ToneVolume)
CONTROL_GROUPS = (
    ControlGroup("maf", "降噪模式", tuple(ControlOption("maf", label, m) for m, label in MAF)),
    ControlGroup("low_latency", "低延迟", tuple(ControlOption("low_latency", label, m) for m, label in LATENCY)),
    ControlGroup("cinema", "影院模式", tuple(ControlOption("cinema", "开" if on else "关", on) for on in (False, True))),
    ControlGroup("multipoint", "蓝牙畅连", tuple(ControlOption("multipoint", "开" if on else "关", on) for on in (False, True))),
    ControlGroup("tone", "语音提示音量", tuple(ControlOption("tone", label, m) for m, label in TONE)),
)


def menu_model(state: ControlState) -> tuple[ControlGroup, ...]:
    groups = []
    for group in CONTROL_GROUPS:
        options = []
        for option in group.options:
            value = int(option.value) if isinstance(option.value, int) else option.value
            current = getattr(state, group.action)
            include = not (group.action == "low_latency" and state.low_latency in (0, 1) and value in (2, 3))
            if include:
                options.append(ControlOption(option.action, option.label, value,
                                             checked=current == value, enabled=state.connected))
        groups.append(ControlGroup(group.action, group.label, tuple(options)))
    return tuple(groups)
```

- [ ] **Step 4: 验证模型与全部原测试**

Run: `python -m pytest tests/test_quick_controls.py tests/test_protocol.py -q`
Expected: 2 passed（若测试函数数量不同，以全部通过为准，不得修改协议测试）。

- [ ] **Step 5: 提交**

```bash
git add quick_controls.py tests/test_quick_controls.py
git commit -m "feat: 提取快捷控制目录与菜单模型"
```

---

### Task 2: 主线程调度、可选托盘适配与幂等生命周期

**Files:**
- Create: `tray_support.py`
- Create: `window_lifecycle.py`
- Test: `tests/test_tray_support.py`
- Test: `tests/test_window_lifecycle.py`

**Interfaces:**
- Consumes: Task 1 的 `menu_model()`；App 将传入 `dispatch(callback)`、`show_window()`、`hide_to_tray()`、`request_exit()`、`snapshot()`、`run_control(action, value)`。
- Produces: `MainThreadDispatcher(root, on_error)` 的 `submit(callback: Callable[[], None]) -> None` 和 `pump() -> None`；`load_tray_backend() -> TrayBackend | None`；`TrayBackend.available`、`TrayBackend.icon_path`、`TrayBackend.create_icon(...)`；`WindowLifecycle(root, tray, on_exit)` 的 `show()`、`hide()`、`toggle()`、`exit()`。

- [ ] **Step 1: 写调度、依赖回退、生命周期失败测试**

```python
import queue
import threading
from window_lifecycle import MainThreadDispatcher, WindowLifecycle


class FakeRoot:
    def __init__(self):
        self.withdrawn = False
        self.destroyed = 0
    def withdraw(self): self.withdrawn = True
    def deiconify(self): self.withdrawn = False
    def lift(self): pass
    def focus_force(self): pass
    def destroy(self): self.destroyed += 1


class FakeTray:
    def __init__(self): self.available = True; self.stopped = 0
    def stop(self): self.stopped += 1


class FakeWorker:
    def __init__(self): self.shutdown_calls = 0
    def shutdown(self): self.shutdown_calls += 1


def test_dispatcher_runs_tray_callback_on_main_thread_and_survives_error():
    root_thread = threading.get_ident()
    seen, errors = [], []
    dispatcher = MainThreadDispatcher(queue.Queue(), root_thread, errors.append)
    worker = threading.Thread(target=lambda: (dispatcher.submit(lambda: seen.append(threading.get_ident())),
                                              dispatcher.submit(lambda: 1 / 0)))
    worker.start(); worker.join()
    while dispatcher.pump(): pass
    assert seen == [root_thread]
    assert len(errors) == 1 and isinstance(errors[0], ZeroDivisionError)


def test_hide_is_not_exit_and_repeated_show_and_exit_are_idempotent():
    root, tray, worker = FakeRoot(), FakeTray(), FakeWorker()
    life = WindowLifecycle(root, tray, worker)
    life.hide()
    assert root.withdrawn and worker.shutdown_calls == 0
    life.show(); life.show()
    assert not root.withdrawn
    life.hide(); life.toggle()
    assert not root.withdrawn
    life.exit(); life.exit()
    assert (tray.stopped, worker.shutdown_calls, root.destroyed) == (1, 1, 1)


def test_unavailable_tray_does_not_hide_window():
    root, tray, worker = FakeRoot(), FakeTray(), FakeWorker()
    tray.available = False
    life = WindowLifecycle(root, tray, worker)
    assert life.hide() is False
    assert not root.withdrawn
    assert worker.shutdown_calls == 0
```

另写 `tests/test_tray_support.py`：

```python
import builtins
from tray_support import load_tray_backend


def test_missing_pystray_returns_unavailable_backend(monkeypatch):
    original = builtins.__import__
    def blocked(name, *args, **kwargs):
        if name == "pystray": raise ImportError("simulated missing dependency")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", blocked)
    backend = load_tray_backend()
    assert backend is not None and backend.available is False
```

- [ ] **Step 2: 运行失败测试**

Run: `python -m pytest tests/test_tray_support.py tests/test_window_lifecycle.py -q`
Expected: 因 `tray_support`、`window_lifecycle` 不存在而 collection error。

- [ ] **Step 3: 实现调度与生命周期**

```python
# window_lifecycle.py
import queue
import threading
from collections.abc import Callable


class MainThreadDispatcher:
    def __init__(self, tasks, owner_thread_id, on_error):
        self.tasks, self.owner_thread_id, self.on_error = tasks, owner_thread_id, on_error
    def submit(self, callback: Callable[[], None]) -> None:
        if threading.get_ident() == self.owner_thread_id:
            callback()
        else:
            self.tasks.put(callback)
    def pump(self) -> bool:
        ran = False
        while True:
            try: callback = self.tasks.get_nowait()
            except queue.Empty: return ran
            ran = True
            try: callback()
            except Exception as exc: self.on_error(exc)


class WindowLifecycle:
    def __init__(self, root, tray, worker):
        self.root, self.tray, self.worker = root, tray, worker
        self.exiting = False
    def show(self):
        if self.exiting: return
        self.root.deiconify(); self.root.lift(); self.root.focus_force()
    def hide(self) -> bool:
        if self.exiting or not self.tray.available: return False
        self.root.withdraw(); return True
    def toggle(self):
        if self.root.withdrawn: self.show()
        else: self.hide()
    def exit(self):
        if self.exiting: return
        self.exiting = True
        self.tray.stop(); self.worker.shutdown(); self.root.destroy()
```

- [ ] **Step 4: 实现可选依赖与 pystray 工厂**

`load_tray_backend()` 必须在函数内 import `pystray` 和 `PIL.Image`，捕获 `ImportError` 并返回 `TrayBackend(available=False, error=str(exc))`，不能在模块导入时崩溃。可用 backend 暴露 `create_icon(title, menu_model, callbacks)`，内部调用：

```python
image = Image.open(self.icon_path)
icon = pystray.Icon("FiilPodsPC", image, title, self._build_menu(menu_model, callbacks))
icon.run_detached()
```

`_build_menu()` 把控制组转换为 `pystray.Menu` / `MenuItem`，checked 用 `lambda item, option=option: option.checked`；动作回调仅 `dispatcher.submit(lambda: callbacks.run_control(option.action, option.value))`，绝不直接访问 Tk。

- [ ] **Step 5: 验证 Green 与原测试**

Run: `python -m pytest tests/test_tray_support.py tests/test_window_lifecycle.py tests/test_quick_controls.py tests/test_multipoint_mock.py -q`
Expected: 全部 passed；无 traceback。

- [ ] **Step 6: 提交**

```bash
git add tray_support.py window_lifecycle.py tests/test_tray_support.py tests/test_window_lifecycle.py
git commit -m "feat: 增加托盘调度与窗口生命周期"
```

---

### Task 3: 接入 App 动作、状态快照与托盘菜单

**Files:**
- Modify: `app.py:42-70, 169-188, 374-538, 578-736, 775-812`
- Test: `tests/test_tray_actions.py`

**Interfaces:**
- Consumes: Task 1 的模型和 Task 2 的 `MainThreadDispatcher` / `WindowLifecycle` / `load_tray_backend()`。
- Produces: `App._control_snapshot() -> ControlState`、`App.run_tray_control(action: str, value: object) -> None`、`App.show_window()`、`App.hide_to_tray()`、`App.request_exit()`；`_on_close()` 仅用于真正退出。

- [ ] **Step 1: 写 App 协调动作失败测试（不创建真 Tk）**

用 `App.__new__(App)` 构造对象，填 `connected`、`cinema_on`、`mp_on`、`maf_expect`、伪 `worker`、伪 `status`、伪 `root.after` 和捕获 `after` 的函数，避免 GUI 与蓝牙。

```python
from unittest.mock import Mock
from app import App
from fiil_protocol import MafMode


def make_app():
    app = App.__new__(App)
    app.connected = True
    app.worker = Mock()
    app.status = Mock()
    app.root = Mock()
    app.root.after = lambda delay, callback: None
    app.cinema_on = Mock(); app.mp_on = Mock()
    return app


def test_tray_maf_uses_existing_setter_and_verified_value():
    app = make_app()
    app.run_tray_control("maf", MafMode.WIND)
    app.worker.submit.assert_called_once()
    command = app.worker.submit.call_args.args[0]
    assert command.kind == "set" and command.payload[0] == "set_maf"
    assert int(command.payload[1]) == 4


def test_tray_boolean_targets_false_even_when_current_is_true():
    app = make_app()
    app.cinema_on.get.return_value = True
    app.run_tray_control("cinema", False)
    app.cinema_on.set.assert_called_once_with(False)
    command = app.worker.submit.call_args.args[0]
    assert command.payload[:2] == ("set_cinema", False)


def test_disconnected_tray_action_does_not_submit_ble_command():
    app = make_app(); app.connected = False
    app.run_tray_control("low_latency", 1)
    app.worker.submit.assert_not_called()
```

补充生命周期接线断言：伪 `WindowLifecycle` 与伪托盘，验证 `root.protocol("WM_DELETE_WINDOW", app.hide_to_tray)` 的处理器不是 `request_exit`；`show_window()` 调用 `lifecycle.show()`；`request_exit()` 调用 `lifecycle.exit()`。

- [ ] **Step 2: 运行失败测试**

Run: `python -m pytest tests/test_tray_actions.py -q`
Expected: FAIL `AttributeError: 'App' object has no attribute 'run_tray_control'`。

- [ ] **Step 3: 实现 App 最小接线**

初始化时创建 `queue.Queue()` 调度队列、dispatcher、backend 和生命周期；在 `_pump()` 中先 `dispatcher.pump()` 再处理 BLE 队列。托盘创建失败只设置 unavailable 并记录状态，不退出 App。

实现：

```python
def _control_snapshot(self):
    return ControlState(connected=self.connected, maf=self.maf_value,
                        low_latency=self.ll_value, cinema=self.cinema_value,
                        multipoint=self.mp_value, tone=self.tone_value)


def run_tray_control(self, action, value):
    if action == "maf": self._on_set_maf(MafMode(value))
    elif action == "low_latency": self._on_set_low_latency(int(value))
    elif action == "cinema":
        self.cinema_on.set(bool(value)); self._on_set_cinema()
    elif action == "multipoint":
        self.mp_on.set(bool(value)); self._on_set_multipoint()
    elif action == "tone": self._on_set_tone(int(value))


def show_window(self): self.lifecycle.show()
def hide_to_tray(self):
    if not self.lifecycle.hide():
        self.status.set("系统托盘不可用；窗口保持显示。真正退出请使用托盘退出或应用退出入口。")
def request_exit(self): self.lifecycle.exit()
```

将 `self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)` 接入初始化；`show_window()` 为托盘默认项；`request_exit()` 为退出项。`WindowLifecycle` 退出回调必须调用 `self.worker.shutdown()`、托盘 `stop()` 和 `root.destroy()` 各一次。

在 `_handle_event()` 的 `conn`、`maf`、`lowlatency`、`cinema`、`multipoint`、`tone` 分支只更新普通 Python 状态字段，再由 `_control_snapshot()` 读取。状态变化时调用托盘 `update_menu()`（若 backend 支持）；不在托盘线程访问 Tk。

把主窗口 MAF、低延迟、提示音量按钮的 `(value, label)` 来源换成 Task 1 的共享目录，保持原 UI 布局、文字、校验延迟和错误提示。

- [ ] **Step 4: 验证 App 测试和全部离线测试**

Run: `python -m pytest tests -q`
Expected: 所有测试 passed；原 5 个离线测试仍通过。
Run: `python -m compileall -q app.py quick_controls.py tray_support.py window_lifecycle.py`
Expected: exit 0。

- [ ] **Step 5: 提交**

```bash
git add app.py tests/test_tray_actions.py
git commit -m "feat: 接入托盘后台和全部快捷切换"
```

---

### Task 4: 依赖、冻结包与 Windows 实际验证

**Files:**
- Modify: `requirements.txt`
- Modify: `build/fiil_pods.spec:10-43`
- Modify: `build/使用说明.txt`

**Interfaces:**
- Consumes: Task 3 的 `load_tray_backend()` 和托盘图标路径。
- Produces: 冻结版包含 `pystray`、Pillow 与图标；可在 Windows 通知区显示后台菜单。

在顶部连接条增加「后台运行」和「退出程序」按钮，分别调用 `hide_to_tray()` 与 `request_exit()`。托盘不可用时点击 × 保持窗口显示，并通过 messagebox 提示「托盘不可用，请使用主窗口的『退出程序』按钮关闭」；因此没有托盘也不会把用户困在后台。

- [ ] **Step 4a: 验证窗口生命周期测试与真实入口**

Run: `python -m pytest tests/test_tray_actions.py tests/test_window_lifecycle.py -q`
Expected: 全部 passed；主窗口退出按钮调用 `request_exit()`，× 调用 `hide_to_tray()`。

- [ ] **Step 4b: 运行完整离线检查**

用 `tests/test_tray_support.py` 新增 `test_declared_runtime_dependencies_cover_tray()`：读取根 `requirements.txt`，去除注释和空行，断言存在名称规范化后为 `pystray` 与 `pillow` 的依赖。

Run: `python -m pytest tests/test_tray_support.py::test_declared_runtime_dependencies_cover_tray -q`
Expected: FAIL（尚未声明）。

- [ ] **Step 2: 添加依赖并验证导入**

在 `requirements.txt` 增加：

```text
pystray>=0.19,<1
pillow>=10,<13
```

Run: `D:\python\python.exe -m pip install -r requirements.txt`
Expected: 安装成功；如网络失败，记录准确错误，不能跳过冻结验证。
Run: `D:\python\python.exe -c "import pystray; from PIL import Image; print('tray imports OK')"`
Expected: 输出 `tray imports OK`。

- [ ] **Step 3: 更新打包并构建**

spec 将 `pystray` 加入 `collect_submodules`，并从 `excludes` 删除 `PIL`。保持 `app.ico` 与脱敏 `known_devices.json` 的 datas 不变。

Run: `D:\python\python.exe -m PyInstaller --clean --noconfirm build\fiil_pods.spec`
Expected: 成功生成 `dist\FiilPodsPC\FiilPodsPC.exe`；没有 pystray/PIL 缺失错误。
Run: `D:\python\python.exe -m pytest tests -q; D:\python\python.exe -m ruff check .`
Expected: 全部 passed / `All checks passed!`。

- [ ] **Step 4: Windows 托盘与退出验证**

启动 EXE，记录进程、窗口和托盘图标。逐项验证：右键菜单显示五类控制、× 隐藏但进程存活、双击恢复、未连接时不能下发控制、退出后进程结束且图标消失。使用 Windows UI Automation / Shell_NotifyIcon 能力检测实际图标，不以“代码看起来正确”替代验证。

若沙箱或当前桌面无法访问通知区，记录精确错误和已验证范围；不能宣称托盘实测通过。真机存在时验证低延迟与 MAF 各一项，查看 ACK 与读回，未执行项目明确标注。

- [ ] **Step 5: 提交**

```bash
git add requirements.txt build/fiil_pods.spec build/使用说明.txt tests/test_tray_support.py
git commit -m "build: 打包系统托盘运行依赖"
```

---

### Task 5: 版本、文档、CI 与 GitHub 发布

**Files:**
- Modify: `README.md`、`CHANGELOG.md`、`build/version_info.txt`、`build/installer.iss`

**Interfaces:**
- Consumes: Task 4 的冻结构建和实际验证结果。
- Produces: 新版本源码提交、GitHub CI、独立新 Release 与安装包附件，不覆盖 v1.0.0。

- [ ] **Step 1: 更新可核对的文档**

README 增加「后台与快捷切换」：×=隐藏、双击=恢复、右键=模式菜单、退出=真正关闭；列明五类控制和未连接行为。CHANGELOG 新增版本条目，区分自动测试、Windows 托盘实测和真机实测，不写未执行的验证。`build/使用说明.txt` 同步说明后台行为。

- [ ] **Step 2: 确定版本并重建安装包**

把 `build/version_info.txt` 的版本四元组和 `build/installer.iss` 的 `AppVersion` / `OutputBaseFilename` 从 `1.0.0` 改为 `1.1.0`；不要改变 AppId。运行：

```powershell
D:\python\python.exe -m PyInstaller --clean --noconfirm build\fiil_pods.spec
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' build\installer.iss
Get-FileHash dist\installer\FiilPodsPC_Setup_1.1.0.exe -Algorithm SHA256
```

Expected: 安装包存在且编译成功；记录精确字节数与 SHA256。

- [ ] **Step 3: 最终自动检查与敏感信息检查**

Run: `D:\python\python.exe -m pytest tests -q`
Run: `D:\python\python.exe -m ruff check .`
Run: `git diff --check`
搜索 `DeepSeek|C:\Users|91499|A8:7A|AA:6F:F7|7A:C8|7A:9D`，排除历史文档和已定义脱敏规则后，不能新增真实隐私信息。
Run: `git status --short`
Expected: 仅预期源文件和文档被修改，无 dist/out/known_devices.json。

- [ ] **Step 4: 提交、推送并检查 CI**

```bash
git add README.md CHANGELOG.md build/version_info.txt build/installer.iss build/使用说明.txt
git commit -m "release: 托盘后台与快捷切换 v1.1.0"
git push origin main
```

通过 GitHub API 读取当前 HEAD 的 `check-runs`，等待 CI 全部完成；若失败，先修复，不创建 Release。

- [ ] **Step 5: 创建独立 v1.1.0 Release**

使用已有 `repo` 权限凭据创建 tag/release，上传 `dist\installer\FiilPodsPC_Setup_1.1.0.exe`，在 release notes 写明托盘功能、依赖、测试范围和未验证限制。通过 API 下载附件重新计算 SHA256，必须与本地一致。不覆盖 v1.0.0，不在 git 中提交 EXE。

Open objectives: 完成完整托盘后台与全部快捷切换（m01283）；设计已确认（m01343）；按本计划实施并交付新软件与安装包。
