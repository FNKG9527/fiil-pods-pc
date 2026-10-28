"""设置窗口（Toplevel）：左侧分页，右侧表单；日志页也在这里。

为什么是独立窗口而不是主窗口的一个标签页：用户要求「添加软件设置页面、把日志移到
设置页面」，而日志要占很大面积、且需要能一边看日志一边操作主窗口。做成独立
Toplevel 后两者可以并排，主窗口也能缩到只放控制。

线程约束：本模块所有方法都必须在 Tk 主线程调用。日志由主窗口的 pump 喂进来，
不在这里读队列。
"""

from __future__ import annotations

import csv
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import settings as st
import settings_form as sf
import ui_theme as theme
from log_store import LogStore


class SettingsWindow:
    """设置窗口。由 App 持有单例，关闭时只 withdraw 不销毁，避免反复重建。"""

    def __init__(
        self,
        root: tk.Tk,
        *,
        store: LogStore,
        current: st.Settings,
        on_apply: Callable[[st.Settings], None],
        on_save: Callable[[st.Settings], None],
        log_text: tk.Text | None = None,
    ) -> None:
        self.root = root
        self.store = store
        self._on_apply = on_apply
        self._on_save = on_save

        self.win = tk.Toplevel(root)
        self.win.title("设置 — FiilPods PC")
        self.win.geometry("900x680")
        self.win.minsize(760, 560)
        self.win.transient(root)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.win.configure(bg=theme.color("bg"))

        self._settings = current
        self._vars: dict[str, tk.Variable] = {}

        self._build()
        self._load_into_widgets(current)
        self.hide()

    # ---------------------------------------------------------------- 构建
    def _build(self) -> None:
        outer = ttk.Frame(self.win, padding=0)
        outer.pack(fill=tk.BOTH, expand=True)

        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))

        self._build_settings_tabs(self.nb)
        self._build_log_tab(self.nb)

        bar = ttk.Frame(outer, padding=(10, 8))
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="恢复默认", command=self._on_reset).pack(side=tk.LEFT)
        ttk.Button(bar, text="取消", command=self._on_cancel).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(bar, text="应用", command=self._on_apply_clicked).pack(side=tk.RIGHT)
        ttk.Button(bar, text="保存", command=self._on_save_clicked).pack(side=tk.RIGHT, padx=(0, 6))
        self.lbl_hint = ttk.Label(bar, text="", foreground=theme.color("muted"))
        self.lbl_hint.pack(side=tk.LEFT, padx=12)

    def _build_settings_tabs(self, nb: ttk.Notebook) -> None:
        for section in sf.SECTIONS:
            page = ttk.Frame(nb, padding=14)
            nb.add(page, text=section.title)

            if section.note:
                ttk.Label(page, text=section.note, foreground=theme.color("muted"),
                          wraplength=780, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 10))

            for row in section.rows:
                self._build_row(page, row)

    def _build_row(self, page: ttk.Frame, row: sf.FormRow) -> None:
        line = ttk.Frame(page)
        line.pack(fill=tk.X, pady=4)

        if row.kind == sf.BOOL:
            var = tk.BooleanVar(value=False)
            self._vars[row.key] = var
            ttk.Checkbutton(line, text=row.label, variable=var).pack(anchor=tk.W)
        else:
            ttk.Label(line, text=row.label, width=22).pack(side=tk.LEFT)
            var = tk.StringVar(value="")
            self._vars[row.key] = var
            bounds = row.bounds() or (0, 999)
            step = 0.5 if row.decimals else 1
            ttk.Spinbox(line, from_=bounds[0], to=bounds[1], increment=step,
                        textvariable=var, width=10).pack(side=tk.LEFT)
            if row.unit:
                ttk.Label(line, text=row.unit, foreground=theme.color("muted")).pack(
                    side=tk.LEFT, padx=(4, 0))

        if row.hint:
            ttk.Label(line, text=row.hint, foreground=theme.color("muted")).pack(
                side=tk.LEFT, padx=(12, 0))

    def _build_log_tab(self, nb: ttk.Notebook) -> None:
        page = ttk.Frame(nb, padding=10)
        nb.add(page, text="日志")

        bar = ttk.Frame(page)
        bar.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(bar, text="复制全部", command=self.copy_log).pack(side=tk.LEFT)
        ttk.Button(bar, text="导出 TXT", command=self.export_txt).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="导出 CSV", command=self.export_csv).pack(side=tk.LEFT)
        ttk.Button(bar, text="清空", command=self.clear_log).pack(side=tk.LEFT, padx=4)
        ttk.Label(bar, text="（日志从主界面移到了这里；关掉主窗口也会继续记录）",
                  foreground=theme.color("muted")).pack(side=tk.LEFT, padx=10)

        wrap = ttk.Frame(page)
        wrap.pack(fill=tk.BOTH, expand=True)
        self.txt = tk.Text(wrap, font=(theme.FONT_MONO, 9), wrap=tk.NONE, undo=False)
        ys = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.txt.yview)
        xs = ttk.Scrollbar(wrap, orient=tk.HORIZONTAL, command=self.txt.xview)
        self.txt.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.txt.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        for tag, color_name in theme.LOG_TAG_COLORS.items():
            self.txt.tag_configure(tag, foreground=theme.color(color_name))

        self.lbl_dropped = ttk.Label(page, text="", foreground=theme.color("warn"))
        self.lbl_dropped.pack(anchor=tk.W, pady=(4, 0))

    # ------------------------------------------------------------ 数据绑定
    def _load_into_widgets(self, current: st.Settings) -> None:
        for key, text in sf.to_values(current).items():
            var = self._vars.get(key)
            if var is None:
                continue
            if isinstance(var, tk.BooleanVar):
                var.set(getattr(current, key))
            else:
                var.set(text)

    def _collect(self) -> tuple[st.Settings, list[str]]:
        raw = {key: var.get() for key, var in self._vars.items()}
        return sf.from_values(self._settings, raw)

    # --------------------------------------------------------------- 动作
    def _on_save_clicked(self) -> None:
        new, warnings = self._collect()
        if warnings:
            messagebox.showwarning("部分设置未生效", "\n".join(warnings), parent=self.win)
        self._settings = new
        self._on_save(new)
        self.lbl_hint.configure(text=f"已保存 {datetime.now():%H:%M:%S}")

    def _on_apply_clicked(self) -> None:
        new, warnings = self._collect()
        if warnings:
            messagebox.showwarning("部分设置未生效", "\n".join(warnings), parent=self.win)
        self._settings = new
        self._on_apply(new)
        self.lbl_hint.configure(text="已应用（未保存到文件）")

    def _on_cancel(self) -> None:
        self._load_into_widgets(self._settings)
        self.hide()

    def _on_reset(self) -> None:
        if not messagebox.askyesno("恢复默认", "把所有设置恢复为出厂默认值？", parent=self.win):
            return
        self._settings = st.DEFAULTS
        self._load_into_widgets(st.DEFAULTS)
        self.lbl_hint.configure(text="已恢复默认（点「保存」才写入文件）")

    # --------------------------------------------------------------- 显示
    def show(self, page: str | None = None) -> None:
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        if page:
            for i in range(self.nb.index("end")):
                if self.nb.tab(i, "text") == page:
                    self.nb.select(i)
                    break

    def hide(self) -> None:
        self.win.withdraw()

    @property
    def visible(self) -> bool:
        try:
            return bool(self.win.winfo_exists()) and self.win.state() != "withdrawn"
        except tk.TclError:
            return False

    # --------------------------------------------------------------- 日志
    def append_log(self, tag: str, clock: str, text: str) -> None:
        """主窗口把每条日志同时写进缓冲与这里（窗口隐藏时 Text 依然存在）。"""
        try:
            self.txt.insert(tk.END, f"{clock}  {text}\n", tag)
            # 超出缓冲容量时 Text 也要跟着裁，否则长时间挂机会越积越多
            lines = int(self.txt.index("end-1c").split(".")[0])
            excess = lines - self.store.capacity
            if excess > 0:
                self.txt.delete("1.0", f"{excess + 1}.0")
            if self.autoscroll:
                self.txt.see(tk.END)
            if self.store.dropped:
                self.lbl_dropped.configure(text=f"（已丢弃最早的 {self.store.dropped} 条）")
        except tk.TclError:
            pass

    #: 由 App 注入，跟随设置里的「自动滚动」
    autoscroll: bool = True

    def copy_log(self) -> None:
        self.win.clipboard_clear()
        self.win.clipboard_append(self.txt.get("1.0", tk.END))

    def export_txt(self, out_dir: Path) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.win,
            initialdir=str(out_dir),
            initialfile=f"fiil_log_{datetime.now():%Y%m%d_%H%M%S}.txt",
            defaultextension=".txt",
            filetypes=[("文本", "*.txt"), ("全部", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(self.txt.get("1.0", tk.END), encoding="utf-8")

    def export_csv(self, out_dir: Path) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.win,
            initialdir=str(out_dir),
            initialfile=f"fiil_log_{datetime.now():%Y%m%d_%H%M%S}.csv",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("全部", "*.*")],
        )
        if not path:
            return
        rows = self.store.csv_rows()
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=["time", "dir", "data", "note"])
            writer.writeheader()
            writer.writerows(rows)

    def clear_log(self) -> None:
        self.txt.delete("1.0", tk.END)
        self.store.clear()
        self.lbl_dropped.configure(text="")

    def sync_from_store(self) -> None:
        """缓冲被外部改动（如缩容）后，用缓冲内容整体重画 Text。"""
        try:
            self.txt.delete("1.0", tk.END)
            for entry in self.store.entries():
                self.txt.insert(tk.END, entry.line() + "\n", entry.tag)
            if self.autoscroll:
                self.txt.see(tk.END)
        except tk.TclError:
            pass


def open_settings(app: Any, page: str | None = None) -> SettingsWindow:
    """惰性创建并显示设置窗口（App 侧只调这个）。"""
    win: SettingsWindow | None = getattr(app, "settings_window", None)
    if win is None or not win.win.winfo_exists():
        win = SettingsWindow(
            app.root,
            store=app.log_store,
            current=app.settings,
            on_apply=app.apply_settings,
            on_save=app.save_settings,
        )
        app.settings_window = win
    win.show(page)
    return win
