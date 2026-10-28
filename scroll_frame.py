"""可滚动的竖向容器。

主窗口左栏（②~⑧ 七个面板）自然高度约 1033px，而 1280x820 窗口里
只能分到约 682px —— 直接 pack 的后果是底部面板被压成一条缝、
「⑧ 静默监听」整个消失（实测 y=637 h=45，⑧ 高度为 0）。

这里用 Canvas + 内嵌 Frame 的常规做法：内容超出视口时出现竖向滚动条，
鼠标滚轮只在指针进入该区域时才接管滚动，避免抢走其他控件的滚轮。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

# 一格滚轮的像素折算：Windows 的 WM_MOUSEWHEEL 一格 = 120，
# 折成 3 行比默认 1 行跟手，又不会一次滚过头。
LINES_PER_NOTCH = 3
WHEEL_DELTA = 120

# 滚动条显隐的容差：scrollregion 与实际可视高度常差 1px，
# 不留容差会让滚动条在刚好放得下时来回闪。容差只能给 1px：
# 给到 2px 就会把「真的多出 2px」也当成放得下，底部又被压掉一截。
SCROLL_TOLERANCE = 1


def wheel_steps(delta: int) -> int:
    """把 Tk 的 <MouseWheel> delta 换成 yview_scroll 的行数。

    Windows 向上滚 delta=+120，而 Canvas 的 yview_scroll 正数表示往下，
    所以符号要反过来。触控板可能发出 delta=0，此时不滚动。
    """
    if delta == 0:
        return 0
    return -(delta // WHEEL_DELTA) * LINES_PER_NOTCH


def needs_scrollbar(content_height: int, viewport_height: int) -> bool:
    """内容是否高到需要滚动条。"""
    return content_height > viewport_height + SCROLL_TOLERANCE


class ScrollFrame(ttk.Frame):
    """竖向滚动容器。把控件放进 `.body`，它会被自动撑到内容高度。"""

    def __init__(self, master: Any, width: int | None = None, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        if width is not None:
            # ttk.Frame 默认会按子控件收缩，固定宽度的栏必须自己锁住
            self.configure(width=width)
            self.pack_propagate(False)
            self.grid_propagate(False)
        self._canvas = tk.Canvas(
            self,
            highlightthickness=0,
            borderwidth=0,
            background=self._background(),
        )
        if width is not None:
            self._canvas.configure(width=width)
        self._scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._on_scroll_set)

        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 滚动条初始隐藏：内容放得下时不该占位，也不该出现
        self._scrollbar_visible = False

        self.body = ttk.Frame(self._canvas)
        self._window = self._canvas.create_window((0, 0), window=self.body, anchor=tk.NW)

        self.body.bind("<Configure>", self._on_body_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._bind_wheel(self)

    # ------------------------------------------------------------------ 内部
    def _background(self) -> str:
        try:
            style = ttk.Style()
            return style.lookup("TFrame", "background") or "systemWindowBody"
        except tk.TclError:
            return "systemWindowBody"

    def _on_body_configure(self, _event: Any = None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._sync_scrollbar()

    def _on_canvas_configure(self, event: Any) -> None:
        # 内嵌窗口始终与视口同宽，否则内容会按自己的自然宽度显示
        self._canvas.itemconfigure(self._window, width=event.width)
        # 用事件里的高度而不是 winfo_height()：<Configure> 触发时
        # winfo_height() 还停留在旧值，会算出错误的滚动条显隐。
        self._sync_scrollbar(viewport=event.height)

    def _on_scroll_set(self, first: str, last: str) -> None:
        self._scrollbar.set(first, last)
        self._sync_scrollbar()

    def _content_height(self) -> int:
        bbox = self._canvas.bbox("all")
        return (bbox[3] - bbox[1]) if bbox else 0

    def _sync_scrollbar(self, viewport: int | None = None) -> None:
        """内容放得下就藏起滚动条，放不下才显示。"""
        height = viewport if viewport is not None else self._canvas.winfo_height()
        need = needs_scrollbar(self._content_height(), height)
        if need and not self._scrollbar_visible:
            self._scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self._scrollbar_visible = True
        elif not need and self._scrollbar_visible:
            self._scrollbar.pack_forget()
            self._scrollbar_visible = False
            self._canvas.yview_moveto(0)

    def _bind_wheel(self, widget: Any) -> None:
        widget.bind("<MouseWheel>", self._on_wheel)
        widget.bind("<Button-4>", self._on_wheel)
        widget.bind("<Button-5>", self._on_wheel)
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _on_wheel(self, event: Any) -> str:
        if not self._scrollbar_visible:
            return "break"
        if getattr(event, "num", None) == 4:
            steps = -LINES_PER_NOTCH
        elif getattr(event, "num", None) == 5:
            steps = LINES_PER_NOTCH
        else:
            steps = wheel_steps(int(getattr(event, "delta", 0)))
        if steps:
            self._canvas.yview_scroll(steps, "units")
        return "break"

    # ------------------------------------------------------------------ 对外
    @property
    def scrollbar_visible(self) -> bool:
        return self._scrollbar_visible

    def scroll_to_top(self) -> None:
        self._canvas.yview_moveto(0)

    def refresh(self) -> None:
        """内容增删后调用：重算 scrollregion 与滚动条显隐。"""
        self._canvas.update_idletasks()
        self._on_body_configure()


def make_scrollable(
    master: Any,
    width: int | None = None,
    build: Callable[[ttk.Frame], None] | None = None,
) -> ScrollFrame:
    """建一个滚动容器，并把 `build` 里的控件都放进它的 body。"""
    frame = ScrollFrame(master, width=width)
    if build is not None:
        build(frame.body)
        frame.refresh()
    return frame
