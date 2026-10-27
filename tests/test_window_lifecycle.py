"""主线程调度与窗口生命周期的离线测试（不创建真 Tk 窗口）。"""

from __future__ import annotations

import queue
import threading

from window_lifecycle import MainThreadDispatcher, WindowLifecycle


class FakeRoot:
    def __init__(self) -> None:
        self.withdrawn = False
        self.destroyed = 0
        self.calls: list[str] = []

    def withdraw(self) -> None:
        self.withdrawn = True

    def deiconify(self) -> None:
        self.withdrawn = False

    def lift(self) -> None:
        self.calls.append("lift")

    def focus_force(self) -> None:
        self.calls.append("focus_force")

    def destroy(self) -> None:
        self.destroyed += 1


class FakeTray:
    def __init__(self) -> None:
        self.available = True
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


class FakeWorker:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def test_dispatcher_runs_tray_callback_on_main_thread_and_survives_error():
    root_thread = threading.get_ident()
    seen: list[int] = []
    errors: list[BaseException] = []
    tasks: queue.Queue = queue.Queue()
    dispatcher = MainThreadDispatcher(tasks, root_thread, errors.append)

    worker = threading.Thread(
        target=lambda: (
            dispatcher.submit(lambda: seen.append(threading.get_ident())),
            dispatcher.submit(lambda: 1 / 0),
        )
    )
    worker.start()
    worker.join()
    while dispatcher.pump():
        pass
    assert seen == [root_thread]
    assert len(errors) == 1 and isinstance(errors[0], ZeroDivisionError)


def test_hide_is_not_exit_and_repeated_show_and_exit_are_idempotent():
    root, tray, worker = FakeRoot(), FakeTray(), FakeWorker()
    life = WindowLifecycle(root, tray, worker)
    assert life.hide() is True
    assert root.withdrawn and worker.shutdown_calls == 0
    life.show()
    life.show()
    assert not root.withdrawn
    life.hide()
    life.toggle()
    assert not root.withdrawn
    life.exit()
    life.exit()
    assert (tray.stopped, worker.shutdown_calls, root.destroyed) == (1, 1, 1)


def test_unavailable_tray_does_not_hide_window():
    root, tray, worker = FakeRoot(), FakeTray(), FakeWorker()
    tray.available = False
    life = WindowLifecycle(root, tray, worker)
    assert life.hide() is False
    assert not root.withdrawn
    assert worker.shutdown_calls == 0
