"""窗口显示/隐藏与真正退出的协调器（不依赖 tkinter 具体类型）。

托盘回调来自 pystray 自己的线程，所有 Tk 操作都必须回到主线程；
这个模块只负责状态机，线程投递由 MainThreadDispatcher 完成。
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any


class MainThreadDispatcher:
    """把任意线程的回调排队，由 Tk 主线程的 after 泵逐个执行。"""

    def __init__(self, tasks: queue.Queue, owner_thread_id: int,
                 on_error: Callable[[BaseException], None]) -> None:
        self.tasks = tasks
        self.owner_thread_id = owner_thread_id
        self.on_error = on_error
        self.closed = False

    def submit(self, callback: Callable[[], None]) -> None:
        if self.closed:
            return
        if threading.get_ident() == self.owner_thread_id:
            self._run(callback)
            return
        self.tasks.put(callback)

    def pump(self) -> bool:
        """执行当前排队的回调，返回是否执行了至少一个。异常交给 on_error，绝不冒泡。"""
        ran = False
        while True:
            try:
                callback = self.tasks.get_nowait()
            except queue.Empty:
                return ran
            ran = True
            self._run(callback)

    def close(self) -> None:
        self.closed = True
        while True:
            try:
                self.tasks.get_nowait()
            except queue.Empty:
                return

    def _run(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as exc:  # noqa: BLE001 - 单个回调失败不能拖垮窗口线程
            self.on_error(exc)


class WindowLifecycle:
    """窗口是否可见、是否已退出的唯一真源。exit 幂等，hide 在无托盘时拒绝。"""

    def __init__(self, root: Any, tray: Any, worker: Any) -> None:
        self.root = root
        self.tray = tray
        self.worker = worker
        self.hidden = False
        self.exiting = False

    def show(self) -> None:
        if self.exiting:
            return
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.hidden = False

    def hide(self) -> bool:
        if self.exiting or not getattr(self.tray, "available", False):
            return False
        self.root.withdraw()
        self.hidden = True
        return True

    def toggle(self) -> None:
        if self.hidden:
            self.show()
        else:
            self.hide()

    def exit(self) -> None:
        if self.exiting:
            return
        self.exiting = True
        try:
            self.tray.stop()
        finally:
            try:
                self.worker.shutdown()
            finally:
                self.root.destroy()
