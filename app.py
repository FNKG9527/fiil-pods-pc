"""FiilPods PC 探针 —— FIIL 耳机 BLE GATT 逆向与控制台（tkinter + Bleak）

运行：
    python app.py

设计约束（来自任务书）：
  * PC 程序是「协议调试探针」，不是最终产品。
  * 每一行发送/接收的字节都要显示来源与响应，供 Android 模块复用。
  * 无法验证的控制功能只保留在「原始 Hex 控制台」，不进入语义化按钮。
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import overview as ov
import settings as st
import ui_state
import ui_theme as theme
from ble_worker import BleWorker, Command, Event
from fiil_protocol import MafMode
from log_store import LogStore
from quick_controls import ControlState, menu_model
from scroll_frame import ScrollFrame
from tray_support import TrayIcon, build_menu_items, load_tray_backend
from window_lifecycle import MainThreadDispatcher, WindowLifecycle

APP_TITLE = "FiilPods PC 探针 — FIIL 耳机 BLE 逆向控制台"
APP_VERSION = "1.4.0"


def _app_dir() -> Path:
    """可写数据目录：打包成 exe 后程序目录通常在 Program Files（只读），
    所以冻结运行时把 out/ 与设备表放到 %LOCALAPPDATA%\\FiilPodsPC；源码运行时仍用脚本目录。"""
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "FiilPodsPC"
    return Path(__file__).resolve().parent


OUT_DIR = _app_dir() / "out"
SETTINGS_FILE = _app_dir() / "settings.json"
WINDOW_FILE = _app_dir() / "window.json"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_TITLE}  v{APP_VERSION}")

        # 设置先加载：窗口尺寸、扫描超时等都取决于它
        self.settings, setting_warnings = st.load(SETTINGS_FILE)
        self.log_store = LogStore(capacity=self.settings.log_max_lines)
        size = ui_state.load(WINDOW_FILE) if self.settings.remember_window else ui_state.WindowSize()
        self.root.geometry(size.geometry())
        self.root.minsize(*ui_state.MIN_SIZE)

        self.evt_q: queue.Queue[Event] = queue.Queue()
        self.cmd_q: queue.Queue[Command] = queue.Queue()
        self.worker = BleWorker(self.evt_q, self.cmd_q)
        self.worker.start()
        self._push_worker_config()

        self.connected = False
        self.scan_results: list[tuple[str, str, int | None, Any]] = []
        self.battery_state: Any = None

        # 写后读回校验：记下期望值，收到回读时比对（None = 当前无待校验项）
        self.maf_expect: int | None = None
        self.cinema_expect: bool | None = None
        self.ll_expect: int | None = None
        self.mp_expect: bool | None = None
        self.tone_expect: int | None = None
        self.ldac_expect: bool | None = None

        # 托盘菜单用的最后已知状态（None = 还没读到，不要编造默认值）
        self.maf_value: int | None = None
        self.ll_value: int | None = None
        self.cinema_value: bool | None = None
        self.mp_value: bool | None = None
        self.tone_value: int | None = None
        self.ldac_value: bool | None = None
        self.ldac_unlocked: bool | None = None

        # 托盘与主线程调度：pystray 在自己的线程跑，所有 Tk 动作都排回主线程
        self._dispatch_q: queue.Queue = queue.Queue()
        self._dispatcher = MainThreadDispatcher(
            self._dispatch_q, threading.get_ident(),
            lambda exc: self._append("err", "", f"!! 托盘回调异常：{exc!r}"),
        )
        backend = load_tray_backend()
        self.tray = TrayIcon(backend, APP_TITLE, self._on_tray_action)
        self.settings_window = None

        self._build_ui()
        self._apply_settings_to_ui()
        self._refresh_overview()
        self._prefill_last_addr()
        self.lifecycle = WindowLifecycle(root, self.tray, self.worker)
        self._install_window_callbacks()

        for warning in setting_warnings:
            self._append("sys", "", f"[设置] {warning}")

        # 托盘必须等界面建好后再启动：不可用时要往日志区写原因
        if backend.available and not self.tray.start(self._tray_items()):
            self._append("sys", "", f"[托盘] 初始化失败：{self.tray.error or '原因未知'}；程序保持普通窗口模式。")
        elif not backend.available:
            self._append("sys", "", f"[托盘] 不可用：{backend.error}")

        self.root.after(60, self._pump)

        # 启动后直接隐藏到托盘（开机自启场景）；没有托盘时不能把用户困在看不见的窗口里
        if self.settings.auto_hide_to_tray and getattr(self.tray, "available", False):
            self.root.after(300, self.hide_to_tray)

    def _prefill_last_addr(self) -> None:
        """启动时把上次连过的地址填好：耳机不广播时一键即可连上，不用去翻日志。"""
        try:
            from ble_worker import _load_known_devices
            table = _load_known_devices()
            if not table:
                return
            addr = max(
                table.items(),
                key=lambda kv: str(kv[1].get("last_connected") or ""),
            )[0]
            self.var_addr.set(addr)
            name = str(table.get(addr, {}).get("name") or "").strip()
            self.status.set(
                f"上次连接：{name or 'FIIL 耳机'} [{addr}] —— 可直接点「连接」"
            )
            # 设置里开了自动连接就直接连，省掉一次手动点击
            if self.settings.auto_connect:
                self.root.after(600, lambda: self.worker.submit(Command("connect", addr)))
                self._append("sys", "", f"[设置] 启动自动连接已开启，正在连接上次设备 {addr}")
        except Exception:  # noqa: BLE001 - 预填失败不影响启动
            pass

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        # v1.4.0 布局：深色品牌条 + 紧凑连接条 + 左右双栏。
        # 原来是三层 LabelFrame（"① 设备连接" 带边框 + 两行控件）占 93px，
        # 现在合成一条 34px 的品牌条和一条 32px 的连接行，省下 27px 给内容区。
        pad = {"padx": theme.SPACE["lg"], "pady": theme.SPACE["md"]}

        self._build_header()
        self._build_connect_bar()

        # ---- 主体：左控制 / 右状态总览 ----
        body = ttk.Frame(self.root)
        body.pack(fill=tk.BOTH, expand=True, **pad)

        left = ScrollFrame(body, width=theme.LEFT_PANE_WIDTH)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, theme.SPACE["lg"]))
        # 左栏 7 个面板堆起来仍会超过窗口高度，套滚动容器：
        # 放得下时无滚动条，放不下时出现竖向滚动条（v1.3.x 已如此，保留）。
        left.pack_propagate(False)
        self.left_scroll = left

        right = ttk.Frame(body)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_left(left.body)
        self._build_right(right)

        # ---- 底部状态栏 ----
        self.status = tk.StringVar(value="就绪。先点「扫描设备」，选中 FIIL 耳机后连接。")
        bar = tk.Label(self.root, textvariable=self.status, anchor=tk.W,
                       bg=theme.color("surface"), fg=theme.color("muted"),
                       font=(theme.FONT_UI, theme.FONT_SIZE_SMALL),
                       padx=theme.SPACE["lg"], pady=theme.SPACE["sm"],
                       highlightthickness=1, highlightbackground=theme.color("border_soft"))
        bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _build_header(self) -> None:
        """深色品牌条：给界面一个视觉锚点，也是「素」的主要解药。"""
        head = tk.Frame(self.root, bg=theme.color("header_bg"))
        head.pack(fill=tk.X, side=tk.TOP)

        inner = tk.Frame(head, bg=theme.color("header_bg"))
        inner.pack(fill=tk.X, padx=theme.SPACE["xl"], pady=theme.SPACE["md"])

        tk.Label(inner, text="FIIL", bg=theme.color("header_bg"),
                 fg=theme.color("header_text"),
                 font=(theme.FONT_UI, theme.FONT_SIZE_BRAND, "bold")).pack(side=tk.LEFT)
        tk.Label(inner, text="  蓝牙控制台", bg=theme.color("header_bg"),
                 fg=theme.color("header_muted"),
                 font=(theme.FONT_UI, theme.FONT_SIZE_BASE)).pack(side=tk.LEFT)
        tk.Label(inner, text=f"v{APP_VERSION}", bg=theme.color("header_bg"),
                 fg=theme.color("header_muted"),
                 font=(theme.FONT_MONO, theme.FONT_SIZE_SMALL)).pack(side=tk.LEFT,
                                                                   padx=(theme.SPACE["md"], 0))

        # 右侧动作按钮：挂在深色条上，主区域就不再被三个按钮占一行
        for text, cmd, padx in (
            ("退出", self.request_exit, (theme.SPACE["sm"], 0)),
            ("后台运行", self.hide_to_tray, (theme.SPACE["sm"], 0)),
            ("⚙ 设置", self.open_settings, (0, 0)),
        ):
            tk.Button(
                inner, text=text, command=cmd,
                bg=theme.color("header_bg"), fg=theme.color("header_muted"),
                activebackground=theme.color("accent"),
                activeforeground=theme.color("header_text"),
                relief=tk.FLAT, borderwidth=0, highlightthickness=0,
                font=(theme.FONT_UI, theme.FONT_SIZE_BASE),
                padx=theme.SPACE["md"], pady=theme.SPACE["xs"], cursor="hand2",
            ).pack(side=tk.RIGHT, padx=padx)

        # 分离线：深色条与浅色主体之间的一道细边
        tk.Frame(self.root, bg=theme.color("accent"), height=2).pack(fill=tk.X)

    def _build_connect_bar(self) -> None:
        """设备连接条：扫描 / 选择 / 连接 / 状态胶囊 + 直连地址。"""
        bar = tk.Frame(self.root, bg=theme.color("card"),
                       highlightthickness=1, highlightbackground=theme.color("border_soft"))
        bar.pack(fill=tk.X, padx=theme.SPACE["lg"], pady=(theme.SPACE["md"], theme.SPACE["sm"]))

        row = ttk.Frame(bar)
        row.pack(fill=tk.X, padx=theme.SPACE["md"], pady=theme.SPACE["sm"])

        ttk.Button(row, text="扫描设备", command=self._on_scan, width=9).pack(side=tk.LEFT)
        self.cmb_dev = ttk.Combobox(row, width=40, state="readonly",
                                    font=(theme.FONT_UI, theme.FONT_SIZE_BASE))
        self.cmb_dev.pack(side=tk.LEFT, padx=theme.SPACE["md"])
        self.btn_conn = ttk.Button(row, text="连接", command=self._on_connect, width=6)
        self.btn_conn.pack(side=tk.LEFT)
        ttk.Button(row, text="断开", command=self._on_disconnect, width=6).pack(
            side=tk.LEFT, padx=theme.SPACE["sm"])

        # 状态胶囊：用 Label 模拟，避免 ttk 主题下 Button 无法着色的限制
        self.lbl_state = tk.Label(row, text="未连接", fg=theme.color("header_text"),
                                  bg=theme.color("idle"),
                                  padx=theme.SPACE["lg"], pady=theme.SPACE["xs"],
                                  font=(theme.FONT_UI, theme.FONT_SIZE_BASE, "bold"))
        self.lbl_state.pack(side=tk.LEFT, padx=(theme.SPACE["md"], 0))

        # ---- 直连地址（扫描不到时的逃生通道）----
        # 实测：FIIL 双模耳机被经典蓝牙占用时不再发 BLE 广播，扫描列表里根本没有它。
        # 但它仍然可以用 BLE 地址直连，所以留一个手填入口，不用等广播。
        row2 = ttk.Frame(bar)
        row2.pack(fill=tk.X, padx=theme.SPACE["md"], pady=(0, theme.SPACE["sm"]))
        ttk.Label(row2, text="直连地址", foreground=theme.color("muted")).pack(side=tk.LEFT)
        self.var_addr = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.var_addr, width=20,
                  font=(theme.FONT_MONO, theme.FONT_SIZE_BASE)).pack(
            side=tk.LEFT, padx=(theme.SPACE["sm"], theme.SPACE["sm"]))
        ttk.Button(row2, text="按地址连接", command=self._on_connect_addr,
                   width=10).pack(side=tk.LEFT)
        ttk.Label(
            row2,
            text="耳机不出现在扫描列表时用；上次连过的地址会自动记住",
            foreground=theme.color("faint"),
            font=(theme.FONT_UI, theme.FONT_SIZE_SMALL),
        ).pack(side=tk.LEFT, padx=theme.SPACE["md"])

    def _card(self, parent: Any, title: str, tone: str = "accent") -> tk.Frame:
        """建一张带左侧色条的白底卡片，返回放控件的内容区。

        为什么不用 ttk.LabelFrame：Windows 的 vista 主题给 LabelFrame 画的是
        带凹槽的方框，七个一模一样的灰框叠在一起就是「素」的观感来源。
        换成白底卡片 + 彩色竖条后，每张卡片有语义色（电量=绿、降噪=蓝、低延迟=橙、
        原始数据=灰），视觉上能一眼分区，内边距也能自己控，比 LabelFrame 省 8~10px。
        """
        card = tk.Frame(parent, bg=theme.color("card"),
                        highlightthickness=1, highlightbackground=theme.color("border_soft"))
        card.pack(fill=tk.X, pady=(0, theme.PANEL_GAP))

        tk.Frame(card, bg=theme.color(tone), width=3).pack(side=tk.LEFT, fill=tk.Y)

        inner = tk.Frame(card, bg=theme.color("card"))
        inner.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                   padx=theme.PANEL_PADX, pady=theme.PANEL_PADY)

        head = tk.Frame(inner, bg=theme.color("card"))
        head.pack(fill=tk.X, pady=(0, theme.SPACE["sm"]))
        tk.Label(head, text=title, bg=theme.color("card"), fg=theme.color("text"),
                 font=(theme.FONT_UI, theme.FONT_SIZE_TITLE, "bold"),
                 anchor=tk.W).pack(side=tk.LEFT)
        #: 卡片标题行右侧的挂载点，供需要额外按钮的卡片使用
        self._card_head = head

        body = tk.Frame(inner, bg=theme.color("card"))
        body.pack(fill=tk.X)
        return body

    def _note(self, parent: Any, text: str, tone: str = "faint",
              wrap: int | None = None) -> None:
        """卡片脚注：小字、淡色、自动换行。

        wrap 默认按左栏宽度算；右栏的卡片宽得多，传 None 会让脚注被折得很窄、
        右边留一大片空白，所以铺在右栏时要显式给更大的 wraplength。
        """
        tk.Label(parent, text=text, bg=theme.color("card"), fg=theme.color(tone),
                 font=(theme.FONT_UI, theme.FONT_SIZE_SMALL),
                 justify=tk.LEFT, anchor=tk.W,
                 wraplength=wrap if wrap is not None else theme.LEFT_PANE_WIDTH - 46).pack(
            fill=tk.X, pady=(theme.SPACE["sm"], 0))

    def _readback(self, parent: Any, var: tk.StringVar) -> None:
        """「读回 xx」小标签组，颜色用品牌蓝。"""
        tk.Label(parent, text="读回", bg=theme.color("card"), fg=theme.color("faint"),
                 font=(theme.FONT_UI, theme.FONT_SIZE_SMALL)).pack(
            side=tk.LEFT, padx=(theme.SPACE["md"], theme.SPACE["xs"]))
        tk.Label(parent, textvariable=var, bg=theme.color("card"),
                 fg=theme.color("accent"),
                 font=(theme.FONT_UI, theme.FONT_SIZE_BASE, "bold")).pack(side=tk.LEFT)

    def _build_left(self, parent: ttk.Frame) -> None:
        # ---- 电量 ----
        body = self._card(parent, "电量", "ok")
        ttk.Button(self._card_head, text="刷新", width=5,
                   command=self._on_refresh_all).pack(side=tk.RIGHT)

        grid = tk.Frame(body, bg=theme.color("card"))
        grid.pack(fill=tk.X)
        # 竖排（标签在上、数值在下）而不是「标签 数值 标签 数值」横排：
        # 横排时 3 组在 430px 宽度里放不下，第三组的数字会被父容器裁掉。
        for col in range(3):
            grid.columnconfigure(col, weight=1, uniform="bat")

        self.bat_vars = {}
        self.bat_labels = {}
        self.bat_bars: dict[str, tk.Canvas] = {}
        for i, (key, label) in enumerate((("left", "左耳"), ("right", "右耳"), ("case", "充电盒"))):
            cell = tk.Frame(grid, bg=theme.color("card"))
            cell.grid(row=0, column=i, sticky="nsew", padx=(0, theme.SPACE["sm"]))
            lbl = tk.Label(cell, text=label, bg=theme.color("card"),
                           fg=theme.color("muted"),
                           font=(theme.FONT_UI, theme.FONT_SIZE_SMALL), anchor=tk.W)
            lbl.pack(fill=tk.X)
            self.bat_labels[key] = lbl
            v = tk.StringVar(value="--")
            self.bat_vars[key] = v
            tk.Label(cell, textvariable=v, bg=theme.color("card"),
                     fg=theme.color("ok"), font=theme.value_font(),
                     anchor=tk.W).pack(fill=tk.X)
            # 数值旁边配一条 4px 的进度条：纯数字太「素」，一根彩色细条能把
            # 「电量」这个读数变成一眼可扫的图形。Canvas 自绘是因为 ttk.Progressbar
            # 在 vista 主题下强制 20px 高且带边框，压不到这么薄。
            bar = tk.Canvas(cell, height=4, bg=theme.color("card_alt"),
                            highlightthickness=0, bd=0)
            bar.pack(fill=tk.X, pady=(theme.SPACE["xs"], 0))
            self.bat_bars[key] = bar

        # ---- 降噪模式 MAF ----
        body = self._card(parent, "降噪模式 MAF", "accent")
        btn_row = tk.Frame(body, bg=theme.color("card"))
        btn_row.pack(fill=tk.X)

        self.maf_var = tk.StringVar(value="--")
        # 五档全部实机验证（exp_maf_five.py → docs/evidence/maf_five_20261025.txt）：
        # 逐档 SET 0→4，ACK 均成功，@3.0s 读回均等于写入值。
        # 文案用 FIIL+ 原文（关闭/降噪/开放/兼听/风中），不用「监听/自适应」等外部叫法。
        modes = [
            (MafMode.OFF, "关闭"),
            (MafMode.NOISE_CANCEL, "降噪"),
            (MafMode.OPEN, "开放"),
            (MafMode.LISTEN, "兼听"),
            (MafMode.WIND, "风中"),
        ]
        for m, label in modes:
            ttk.Button(btn_row, text=label, width=6,
                       command=lambda mm=m: self._on_set_maf(mm)).pack(
                side=tk.LEFT, padx=(0, theme.SPACE["xs"]))
        self._readback(btn_row, self.maf_var)

        self._note(body, "五档均已实测通过，切换约 2s 生效。")

        # ---- 模式开关（影院 / 畅连 / LDAC 三个独立开关并作一行一组）----
        # 三者都是「勾选框 + 读回值」的同构结构，分三张卡片会各多出 30px 标题与
        # 内边距；合并成一张卡后省下约 90px 竖向空间，语义上也确实同属「开关类」。
        body = self._card(parent, "模式开关", "accent")

        self.cinema_on = tk.BooleanVar(value=False)
        self.cinema_state = tk.StringVar(value="--")
        crow = tk.Frame(body, bg=theme.color("card"))
        crow.pack(fill=tk.X)
        self.chk_cinema = ttk.Checkbutton(crow, text="影院模式", variable=self.cinema_on,
                                          command=self._on_set_cinema)
        self.chk_cinema.pack(side=tk.LEFT)
        self._readback(crow, self.cinema_state)

        self.mp_on = tk.BooleanVar(value=False)
        self.mp_state = tk.StringVar(value="--")
        mrow = tk.Frame(body, bg=theme.color("card"))
        mrow.pack(fill=tk.X, pady=(theme.SPACE["xs"], 0))
        self.chk_mp = ttk.Checkbutton(mrow, text="蓝牙畅连", variable=self.mp_on,
                                      command=self._on_set_multipoint)
        self.chk_mp.pack(side=tk.LEFT)
        self._readback(mrow, self.mp_state)

        self.ldac_on = tk.BooleanVar(value=False)
        self.ldac_state = tk.StringVar(value="--")
        drow = tk.Frame(body, bg=theme.color("card"))
        drow.pack(fill=tk.X, pady=(theme.SPACE["xs"], 0))
        self.chk_ldac = ttk.Checkbutton(drow, text="LDAC 高清", variable=self.ldac_on,
                                        command=self._on_set_ldac)
        self.chk_ldac.pack(side=tk.LEFT)
        self._readback(drow, self.ldac_state)

        # LDAC 未激活时的告警行：平时是空字符串，不占高度（pack_forget 会被反复触发，
        # 用空文本更省事，两行说明的空 Label 在紧凑布局里也就 2px）
        self.ldac_lock_var = tk.StringVar(value="")
        self.lbl_ldac_lock = tk.Label(
            body, textvariable=self.ldac_lock_var, bg=theme.color("card_alt"),
            fg=theme.color("warn"), font=(theme.FONT_UI, theme.FONT_SIZE_SMALL),
            justify=tk.LEFT, anchor=tk.W,
            wraplength=theme.LEFT_PANE_WIDTH - 60,
            padx=theme.SPACE["sm"], pady=theme.SPACE["xs"])
        self.lbl_ldac_lock.pack(fill=tk.X, pady=(theme.SPACE["sm"], 0))

        self._note(body, "畅连切换会断链 10~20s 后自动重连；LDAC 需先在手机 App 激活，"
                         "与低延迟互斥。")

        # ---- 低延迟 ----
        body = self._card(parent, "低延迟模式", "warn")
        lrow = tk.Frame(body, bg=theme.color("card"))
        lrow.pack(fill=tk.X)

        self.ll_var = tk.StringVar(value="--")
        self.ll_btns: dict[int, ttk.Button] = {}
        # Mate 机型（含本机 Key Max）只有 0=关闭 / 1=开启两档 —— 实测 SET 2/3 被耳机拒绝。
        # 按钮先按 Mate 两档建；若读回出现 2/3（说明是 GAIA 机型）再动态补上音乐/视频。
        for val, label in ((0, "关闭"), (1, "开启（游戏）")):
            b = ttk.Button(lrow, text=label, width=11,
                           command=lambda v=val: self._on_set_low_latency(v))
            b.pack(side=tk.LEFT, padx=(0, theme.SPACE["xs"]))
            self.ll_btns[val] = b
        self._readback(lrow, self.ll_var)

        self._note(body, "Mate 机型只有两档，下发音乐/视频会被 ACK 拒绝（不是没生效）。", "warn")

        # ---- 语音提示音量 ----
        body = self._card(parent, "语音提示音量", "accent")
        trow = tk.Frame(body, bg=theme.color("card"))
        trow.pack(fill=tk.X)
        self.tone_var = tk.StringVar(value="--")
        self.tone_btns: dict[int, ttk.Button] = {}
        # 线值反序：0=高 / 1=中 / 2=低 / 3=静音（FIIL 原文 f43_select_operate_volume）
        for val, label in ((0, "高"), (1, "中"), (2, "低"), (3, "静音")):
            b = ttk.Button(trow, text=label, width=6,
                           command=lambda v=val: self._on_set_tone(v))
            b.pack(side=tk.LEFT, padx=(0, theme.SPACE["xs"]))
            self.tone_btns[val] = b
        self._readback(trow, self.tone_var)

    def _build_right(self, parent: ttk.Frame) -> None:
        """右侧：状态总览卡片。

        v1.2.0 把原来的「收发日志 / 解析结果」两个标签页整个搬进了设置窗口的
        「日志」页 —— 主窗口右侧改成一眼能看完的状态总览，不用再切标签。
        """
        box = tk.Frame(parent, bg=theme.color("card"),
                       highlightthickness=1, highlightbackground=theme.color("border_soft"))
        box.pack(fill=tk.BOTH, expand=True)

        head = tk.Frame(box, bg=theme.color("card"))
        head.pack(fill=tk.X, padx=theme.SPACE["lg"], pady=(theme.SPACE["md"], theme.SPACE["sm"]))
        tk.Label(head, text="状态总览", bg=theme.color("card"), fg=theme.color("text"),
                 font=(theme.FONT_UI, theme.FONT_SIZE_TITLE, "bold")).pack(side=tk.LEFT)
        tk.Label(head, text="连接后自动读取", bg=theme.color("card"),
                 fg=theme.color("faint"),
                 font=(theme.FONT_UI, theme.FONT_SIZE_SMALL)).pack(
            side=tk.LEFT, padx=(theme.SPACE["sm"], 0))
        ttk.Button(head, text="刷新", command=self._on_refresh_all, width=6).pack(side=tk.RIGHT)

        # 卡片容器：3 列网格，随窗口宽度自适应
        grid = tk.Frame(box, bg=theme.color("card"))
        grid.pack(fill=tk.X, padx=theme.SPACE["md"], pady=0)
        for col in range(3):
            grid.columnconfigure(col, weight=1, uniform="card")

        self.overview_cards: dict[str, tuple[tk.Frame, tk.Label, tk.Label]] = {}
        #: 每张卡片左侧的语义色条（随 tone 变色）
        self.overview_stripes: dict[str, tk.Frame] = {}
        #: 卡片内容区（随 tone 换淡色底）
        self.overview_inner: dict[str, tk.Frame] = {}
        self._overview_grid = grid

        # 原始 Hex 放在右栏：左侧 6 张卡片已经填满左栏高度，而右栏在 3 行总览卡片
        # 下面空着一大片。把「手写字节」这种宽表格性质的输入搬到右侧，既利用了空白，
        # 也让右侧从「只读总览」变成「总览 + 高级工具」，两栏高度也平衡了。
        self._build_hex_panel(box)

        # 兜底提示：日志搬去设置页之后，用户找不到日志时这里要指路
        tip = tk.Frame(box, bg=theme.color("card_alt"))
        tip.pack(fill=tk.X, padx=theme.SPACE["md"], pady=(theme.SPACE["sm"], theme.SPACE["md"]))
        tk.Label(
            tip, bg=theme.color("card_alt"), fg=theme.color("faint"),
            font=(theme.FONT_UI, theme.FONT_SIZE_SMALL), justify=tk.LEFT, anchor=tk.W,
            text="收发日志（原始字节）与解析结果在「⚙ 设置 → 日志」页，关掉主窗口仍继续记录。",
        ).pack(fill=tk.X, padx=theme.SPACE["sm"], pady=theme.SPACE["xs"])

    def _build_hex_panel(self, parent: Any) -> None:
        """右栏的高级工具区：手写字节 + 静默监听。

        这两个功能都属于逆向取证（发原始命令 / 被动收原始字节），合成一张卡片，
        与左栏的「日常使用」开关区分开。
        """
        wrap = 640  # 右栏比左栏宽得多，脚注按右栏宽度折行
        card = tk.Frame(parent, bg=theme.color("card"),
                        highlightthickness=1, highlightbackground=theme.color("border_soft"))
        card.pack(fill=tk.X, padx=theme.SPACE["md"], pady=(theme.SPACE["sm"], 0))

        inner = tk.Frame(card, bg=theme.color("card"))
        inner.pack(fill=tk.X, padx=theme.PANEL_PADX, pady=theme.PANEL_PADY)

        head = tk.Frame(inner, bg=theme.color("card"))
        head.pack(fill=tk.X, pady=(0, theme.SPACE["sm"]))
        tk.Label(head, text="原始 Hex", bg=theme.color("card"), fg=theme.color("text"),
                 font=(theme.FONT_UI, theme.FONT_SIZE_TITLE, "bold")).pack(side=tk.LEFT)
        tk.Label(head, text="手写字节直接发到耳机 ff17 特征值", bg=theme.color("card"),
                 fg=theme.color("faint"),
                 font=(theme.FONT_UI, theme.FONT_SIZE_SMALL)).pack(
            side=tk.LEFT, padx=(theme.SPACE["sm"], 0))

        erow = tk.Frame(inner, bg=theme.color("card"))
        erow.pack(fill=tk.X, pady=(theme.SPACE["xs"], 0))
        self.ent_hex = ttk.Entry(erow, font=(theme.FONT_MONO, theme.FONT_SIZE_BASE))
        self.ent_hex.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.ent_hex.insert(0, "00 27 01 00 02 01 00")
        ttk.Button(erow, text="发送", width=6, command=self._on_send_hex).pack(
            side=tk.LEFT, padx=(theme.SPACE["sm"], 0))

        # 预设一行放得下 5 个：右栏有 700+ px，不像左栏 430px 那样必须拆两行
        quick = tk.Frame(inner, bg=theme.color("card"))
        quick.pack(fill=tk.X, pady=(theme.SPACE["sm"], 0))
        presets = [
            ("setUnlock", "00 27 01 00 04 FF 02 02 34"),
            ("GET_VERSION", "00 27 01 00 02 02 00"),
            ("GET_EQ", "00 27 01 00 02 04 00"),
            ("SET_MAF=1", "00 2C 01 00 01 01"),
            ("SET_CI=1", "00 32 01 00 01 01"),
        ]
        for label, hx in presets:
            ttk.Button(quick, text=label, width=12,
                       command=lambda h=hx: self._fill_hex(h)).pack(
                side=tk.LEFT, padx=(0, theme.SPACE["xs"]))

        self._note(inner, "帧结构 [seq][cid][type][rsv][lenF][cmdId][plen][payload…]；"
                          "上面所有按钮最终都是拼出这样一串字节。", "faint", wrap=wrap)

        sep = tk.Frame(inner, bg=theme.color("border_soft"), height=1)
        sep.pack(fill=tk.X, pady=(theme.SPACE["md"], theme.SPACE["sm"]))

        lrow = tk.Frame(inner, bg=theme.color("card"))
        lrow.pack(fill=tk.X)
        self.listen_secs = tk.StringVar(value="60")
        tk.Label(lrow, text="静默监听", bg=theme.color("card"), fg=theme.color("muted"),
                 font=(theme.FONT_UI, theme.FONT_SIZE_SMALL)).pack(
            side=tk.LEFT, padx=(0, theme.SPACE["sm"]))
        ttk.Spinbox(lrow, from_=10, to=300, width=5,
                    textvariable=self.listen_secs).pack(side=tk.LEFT)
        tk.Label(lrow, text="秒，只收不发，用于抓取耳机主动上报", bg=theme.color("card"),
                 fg=theme.color("muted"),
                 font=(theme.FONT_UI, theme.FONT_SIZE_SMALL)).pack(
            side=tk.LEFT, padx=(theme.SPACE["xs"], theme.SPACE["md"]))
        ttk.Button(lrow, text="开始", width=6, command=self._on_listen).pack(
            side=tk.LEFT, padx=(0, theme.SPACE["xs"]))
        ttk.Button(lrow, text="停止", width=6,
                   command=lambda: self.worker.submit(Command("stop_listen"))).pack(
            side=tk.LEFT)

    def _make_card(self, key: str, label: str, row: int, col: int) -> None:
        """建一张总览卡片：左侧色条 + 淡色底 + 小标签 + 大数值。

        底色不只是装饰，它是「一眼扫完状态」的关键：tone 决定这条竖条和数值的
        颜色，未连接时整张卡片退成灰白，连上后各自亮起来 —— 七张卡片并排时，
        扫一眼颜色就知道哪项正常、哪项要留意，比读七个文字快得多。
        """
        card = tk.Frame(self._overview_grid, bg=theme.color("card"),
                        highlightthickness=1, highlightbackground=theme.color("border_soft"))
        card.grid(row=row, column=col, sticky="nsew",
                  padx=theme.SPACE["xs"], pady=theme.SPACE["xs"])
        # 设置里关掉总览时，后建的卡片也要立刻隐藏，不能等下次应用设置
        if not self.settings.show_status_overview:
            card.grid_remove()

        # 左侧 3px 语义色条：与左栏卡片的色条同一套语言
        stripe = tk.Frame(card, bg=theme.color("border_soft"), width=3)
        stripe.pack(side=tk.LEFT, fill=tk.Y)

        inner = tk.Frame(card, bg=theme.color("card"))
        inner.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        lbl = tk.Label(inner, text=label, bg=theme.color("card"),
                       fg=theme.color("muted"),
                       font=(theme.FONT_UI, theme.FONT_SIZE_SMALL), anchor=tk.W)
        lbl.pack(fill=tk.X, padx=theme.CARD_PADX, pady=(theme.CARD_PADY, 0))

        val = tk.Label(inner, text="--", bg=theme.color("card"),
                       fg=theme.color("idle"), font=theme.value_font(), anchor=tk.W)
        val.pack(fill=tk.X, padx=theme.CARD_PADX, pady=(0, theme.CARD_PADY))

        self.overview_cards[key] = (card, lbl, val)
        self.overview_stripes[key] = stripe
        self.overview_inner[key] = inner

    def _refresh_overview(self) -> None:
        """按当前已知状态重画总览。未知项一律 --，绝不用默认值冒充读数。"""
        items = ov.build_overview(
            connected=self.connected,
            maf=self.maf_value,
            low_latency=self.ll_value,
            cinema=self.cinema_value,
            multipoint=self.mp_value,
            tone=self.tone_value,
            battery=self.battery_state,
            ldac=self.ldac_value,
            ldac_unlocked=self.ldac_unlocked,
        )
        for i, item in enumerate(items):
            if item.key not in self.overview_cards:
                self._make_card(item.key, item.label, i // 3, i % 3)
            _card, lbl, val = self.overview_cards[item.key]
            lbl.configure(text=item.label)
            val.configure(text=item.value, fg=theme.tone_color(item.tone))
            # 色条与底色跟着 tone 走：连上且正常时是彩色，未读取时退成灰白。
            # 这样七张卡片不是七块一样的白板，而是一眼能分出的状态矩阵。
            self._paint_card(item.key, item.tone)

    def _paint_card(self, key: str, tone: str) -> None:
        """按 tone 重染一张总览卡片的色条与底色。"""
        stripe = self.overview_stripes.get(key)
        inner = self.overview_inner.get(key)
        if stripe is None or inner is None:
            return
        # 未知/关闭态不染色，保持中性白底，避免整屏花花绿绿
        active = tone in ("ok", "warn", "error")
        stripe.configure(bg=theme.color(tone) if active else theme.color("border_soft"))
        # tone_soft 返回的是色值，不是色名，不能再过一次 theme.color()
        soft = theme.tone_soft(tone) if active else theme.color("card")
        inner.configure(bg=soft)
        for child in inner.winfo_children():
            try:
                child.configure(bg=soft)
            except tk.TclError:
                pass

    # -------------------------------------------------------------- 事件泵
    def _pump(self) -> None:
        self._dispatcher.pump()
        dirty_overview = False
        try:
            while True:
                evt = self.evt_q.get_nowait()
                self._handle_event(evt)
                if evt.kind in ("conn", "maf", "lowlatency", "cinema", "multipoint",
                                "tone", "battery", "ldac", "ldac_unlock"):
                    dirty_overview = True
                if evt.kind in ("conn", "maf", "lowlatency", "cinema", "multipoint", "tone",
                                "ldac", "ldac_unlock"):
                    self._refresh_tray_menu()
        except queue.Empty:
            pass
        if dirty_overview:
            self._refresh_overview()
        self.root.after(60, self._pump)

    # -------------------------------------------------------------- 托盘协调
    def _control_snapshot(self) -> ControlState:
        return ControlState(
            connected=self.connected,
            maf=self.maf_value,
            low_latency=self.ll_value,
            cinema=self.cinema_value,
            multipoint=self.mp_value,
            tone=self.tone_value,
            ldac=self.ldac_value,
            ldac_unlocked=self.ldac_unlocked,
        )

    def _tray_items(self) -> list:
        return build_menu_items(
            menu_model(self._control_snapshot()),
            window_visible=not self.lifecycle.hidden,
        )

    def _refresh_tray_menu(self) -> None:
        if getattr(self.tray, "available", False):
            self.tray.update(self._tray_items())

    def _on_tray_action(self, item) -> None:
        """pystray 线程的回调入口：只投递，不碰 Tk。"""
        self._dispatcher.submit(lambda: self._apply_tray_action(item))

    def _apply_tray_action(self, item) -> None:
        action = item.action
        if action == "show":
            self.show_window()
        elif action == "hide":
            self.hide_to_tray()
        elif action == "exit":
            self.request_exit()
        elif action == "connect":
            self.show_window()
            self._on_connect_addr()
        elif action == "disconnect":
            self._on_disconnect()
        elif action == "refresh":
            self._on_refresh_all()
        elif action.startswith("set:"):
            self.run_tray_control(action.split(":", 1)[1], item.value)

    def run_tray_control(self, action: str, value: Any) -> None:
        """复用主窗口的既有动作，不另造协议帧；未连接时静默忽略而不是弹窗。"""
        if not self.connected:
            self.status.set("未连接：快捷切换需要先连接耳机。")
            return
        if action == "maf":
            self._on_set_maf(MafMode(int(value)))
        elif action == "low_latency":
            self._on_set_low_latency(int(value))
        elif action == "cinema":
            self.cinema_on.set(bool(value))
            self._on_set_cinema()
        elif action == "multipoint":
            self.mp_on.set(bool(value))
            self._on_set_multipoint()
        elif action == "tone":
            self._on_set_tone(int(value))
        elif action == "ldac":
            self.ldac_on.set(bool(value))
            self._on_set_ldac()

    # -------------------------------------------------------------- 设置
    def open_settings(self, page: str | None = None) -> None:
        import settings_window as sw

        self.settings_window = sw.open_settings(self, page)
        self.settings_window.autoscroll = bool(self.settings.autoscroll)
        # 打开时把缓冲里的历史日志整体灌进去，用户看到的是完整记录而不是打开之后的增量
        self.settings_window.sync_from_store()

    def apply_settings(self, new: st.Settings) -> None:
        """「应用」：立刻生效但不写文件。"""
        self.settings = new
        self._apply_settings_to_ui()
        self._push_worker_config()

    def save_settings(self, new: st.Settings) -> None:
        """「保存」：写文件 + 立刻生效。写失败要让用户知道，不能静默吞掉。"""
        self.settings = new
        self._apply_settings_to_ui()
        self._push_worker_config()
        try:
            st.save(SETTINGS_FILE, new)
        except OSError as exc:
            messagebox.showerror("保存失败", f"设置文件写入失败：\n{exc}", parent=self.root)

    def _push_worker_config(self) -> None:
        """把连接相关设置推给后台线程（它是唯一真正连蓝牙的地方）。"""
        try:
            self.worker.submit(Command("config", (self.settings.scan_timeout,
                                                  self.settings.connect_timeout)))
        except Exception:  # noqa: BLE001 - 后台线程已退出时忽略
            pass

    def _apply_settings_to_ui(self) -> None:
        """把设置里影响界面的部分同步到控件上（幂等，可反复调）。"""
        self.log_store.set_capacity(self.settings.log_max_lines)
        win = self.settings_window
        if win is not None and getattr(win, "win", None) is not None:
            win.autoscroll = bool(self.settings.autoscroll)
            try:
                win.txt.configure(font=(theme.FONT_MONO, self.settings.log_font_size))
            except tk.TclError:
                pass
        if not self.settings.show_status_overview:
            for _card, _lbl, _val in self.overview_cards.values():
                _card.grid_remove()
        else:
            for _card, _lbl, _val in self.overview_cards.values():
                _card.grid()
        self._apply_ldac_lock()

    # -------------------------------------------------------------- 窗口
    def _install_window_callbacks(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.root.bind("<Configure>", self._on_configure)

    def _on_configure(self, event: Any) -> None:
        """只记窗口自身的尺寸变化（子控件也会触发 <Configure>，要过滤掉）。"""
        if event.widget is not self.root:
            return
        self._last_size = (int(event.width), int(event.height))

    def _save_window_size(self) -> None:
        if not self.settings.remember_window:
            return
        width, height = getattr(self, "_last_size", ui_state.DEFAULT_SIZE)
        try:
            ui_state.save(WINDOW_FILE, ui_state.WindowSize(width, height))
        except OSError:
            pass

    def show_window(self) -> None:
        if self.lifecycle.exiting:
            return
        self.lifecycle.show()
        self._refresh_tray_menu()

    def hide_to_tray(self) -> None:
        if self.lifecycle.hide():
            self._refresh_tray_menu()
            return
        if self.lifecycle.exiting:
            return
        # 没有托盘时绝不假装已进后台：保持窗口并说明退出方式
        messagebox.showinfo(
            "系统托盘不可用",
            "当前环境无法创建托盘图标，窗口已保持显示。\n"
            "如需关闭程序，请点主窗口的「退出程序」。",
        )

    def request_exit(self) -> None:
        if self.settings.confirm_before_exit and not self.lifecycle.exiting:
            if not messagebox.askyesno("退出", "确定退出 FiilPods PC？\n退出后将断开与耳机的连接。"):
                return
        self._save_window_size()
        try:
            self._dispatcher.close()
        except Exception:  # noqa: BLE001
            pass
        self.lifecycle.exit()

    def _draw_battery_bar(self, slot: str, percent: int | None) -> None:
        """把电量百分比画成一条细进度条。低于 20% 转红，20~40% 转橙。

        颜色分档不是装饰：电量是这张卡里唯一会随时间恶化的读数，用颜色区分
        「正常 / 该充电了 / 快没电」比只给数字更快被注意到。
        """
        bar = self.bat_bars.get(slot)
        if bar is None:
            return
        bar.delete("all")
        if percent is None:
            return
        pct = max(0, min(100, int(percent)))
        width = bar.winfo_width()
        if width <= 1:
            # 首次 set 时控件还没 geometry 完，用自然宽度占位，回调里会再画一次
            width = 120
        if pct >= 40:
            fill = theme.color("ok")
        elif pct >= 20:
            fill = theme.color("warn")
        else:
            fill = theme.color("error")
        bar.create_rectangle(0, 0, int(width * pct / 100), 4, fill=fill, width=0)

    def _handle_event(self, e: Event) -> None:
        """把后台线程发来的事件翻译成界面状态。"""
        if e.kind == "log":
            self._append("sys", e.clock, e.text)
            # 连上后读到真实型号（GET_DEVICE_NAME）时，把扫描列表那一项就地改名，
            # 免得下次还要靠「误打误撞点 MAC」才找得到耳机。
            if e.text.startswith("设备名："):
                self._rename_current_device(e.text.split("：", 1)[1].strip())
        elif e.kind == "tx":
            self._append("tx", e.clock, "TX  " + e.text)
        elif e.kind == "rx":
            self._append("rx", e.clock, "RX  " + e.text)
        elif e.kind == "error":
            self._append("err", e.clock, "!!  " + e.text)
            self.status.set(e.text.splitlines()[0][:160])
        elif e.kind == "state":
            self.lbl_state.configure(text=e.text)
        elif e.kind == "conn":
            self.connected = bool((e.payload or {}).get("connected"))
            switching = "切换中" in (e.text or "")
            tone = "warn" if switching else ("ok" if self.connected else "error")
            # 状态胶囊是「色块 + 白字」的样式：底色跟着连接态走（切换中橙、
            # 已连接绿、掉线红），而不是只改文字颜色 —— 只改字色时底色永远是灰的，
            # 远看还是一坨灰块，这正是「素」的一部分。
            self.lbl_state.configure(text=e.text, bg=theme.color(tone),
                                     fg=theme.color("header_text"))
            if not self.connected and not switching:
                # 真正的掉线才清空读数；畅连主从互换是预期行为，界面保留旧值避免闪烁
                self.battery_state = None
                self.maf_value = None
                self.ll_value = None
                self.cinema_value = None
                self.mp_value = None
                self.tone_value = None
                for v in self.bat_vars.values():
                    v.set("--")
                self.maf_var.set("--")
                self.cinema_state.set("--")
        elif e.kind == "scan":
            self.scan_results = e.payload or []
            items = []
            for row in self.scan_results:
                addr, name, rssi = row[0], row[1], row[2]
                oui_hit = row[4] if len(row) > 4 else False
                is_fiil = oui_hit or "FIIL" in (name or "").upper()
                tag = "★ " if is_fiil else "  "
                if rssi is not None:
                    rssi_txt = f"RSSI {rssi}"
                elif row[3] is None:
                    # 来自 known_devices.json 的补录行：耳机此刻没广播，没有 RSSI
                    rssi_txt = "未广播·已知设备"
                else:
                    rssi_txt = "RSSI ?"
                items.append(f"{tag}{name or '(无名)'}  [{addr}]  {rssi_txt}")
            self.cmb_dev["values"] = items
            if items:
                fiil = [i for i, it in enumerate(items) if it.startswith("★")]
                self.cmb_dev.current(fiil[0] if fiil else 0)
            self.status.set(e.text)
            self._append("sys", e.clock, f"[扫描] {e.text}")
            if any(it.startswith("★") for it in items):
                self._append("sys", e.clock,
                             "[扫描] ★ 标记 = FIIL（OUI B0:F1:A3 / FF:F1:A3 或名称命中）。"
                             "BLE 广播常不带名字，连上后会自动读取真实型号。")
            if not any(it.startswith("★") for it in items):
                # 实测：耳机被经典蓝牙占用时会停止 BLE 广播，扫不到是常态而非故障。
                # 这里必须给出可操作提示，否则用户只会看到一屏无名 MAC。
                self._append("sys", e.clock,
                             "[扫描] 没有发现 FIIL 设备。若耳机正被电脑/手机用蓝牙连着，"
                             "它会停止 BLE 广播 —— 请用上方「直连地址」连接"
                             "（上次连过的地址已自动预填）。")
        elif e.kind == "battery":
            b = e.payload
            self.battery_state = b
            if b.single is not None:
                # 单电池机型（Key Max / Aviator 等 F042·F046 族）：只有一个电池，
                # 官方源码 F046HomeFragment.setElectricity 只取 payload[1]。
                # 不能摊成「左 -- / 右 xx% / 盒 --」，那是三耳机型的语义。
                self.bat_labels["left"].configure(text="耳机电量")
                self.bat_vars["left"].set(f"{b.single}%")
                self._draw_battery_bar("left", b.single)
                for key in ("right", "case"):
                    self.bat_labels[key].configure(text="")
                    self.bat_vars[key].set("")
                    self._draw_battery_bar(key, None)
            else:
                self.bat_labels["left"].configure(text="左耳")
                self.bat_labels["right"].configure(text="右耳")
                self.bat_labels["case"].configure(text="充电盒")
                self.bat_vars["left"].set("--" if b.left is None else f"{b.left}%")
                self.bat_vars["right"].set("--" if b.right is None else f"{b.right}%")
                self.bat_vars["case"].set("--" if b.case is None else f"{b.case}%")
                self._draw_battery_bar("left", b.left)
                self._draw_battery_bar("right", b.right)
                self._draw_battery_bar("case", b.case)
            self._parse_line(f"电量  {e.text}")
        elif e.kind == "maf":
            payload = e.payload or {}
            val = payload.get("value")
            label = payload.get("label", e.text)
            self.maf_value = val
            self.maf_var.set(f"{label} ({val})" if val is not None else str(label))
            self._parse_line(e.text)
            # 写后校验：把读回值与会话里记下的「期望值」比对
            if self.maf_expect is not None:
                if val == self.maf_expect:
                    self.status.set(f"MAF 校验通过：{label}")
                else:
                    self.status.set(
                        f"MAF 未确认：期望 {self.maf_expect}，读回 {val}（2s 内读回旧值属正常）")
                self.maf_expect = None
        elif e.kind == "cinema":
            on = bool((e.payload or {}).get("on"))
            self.cinema_value = on
            self.cinema_on.set(on)
            self.cinema_state.set("开" if on else "关")
            self._parse_line(e.text)
            if self.cinema_expect is not None:
                self.status.set(
                    "影院 校验通过" if on == self.cinema_expect
                    else f"影院 未确认：期望 {self.cinema_expect}，读回 {on}")
                self.cinema_expect = None
        elif e.kind == "parse":
            self._parse_line(e.text)
        elif e.kind == "lowlatency":
            payload = e.payload or {}
            val = payload.get("value")
            label = payload.get("label", e.text)
            self.ll_value = val
            self.ll_var.set(f"{label}" if val is not None else "--")
            self._parse_line(e.text)
            self._ensure_ll_buttons(val)
            if self.ll_expect is not None:
                self.status.set(
                    "低延迟 校验通过" if val == self.ll_expect
                    else f"低延迟 未确认：期望 {self.ll_expect}，读回 {val}")
                self.ll_expect = None
        elif e.kind == "multipoint":
            on = bool((e.payload or {}).get("on"))
            self.mp_value = on
            self.mp_on.set(on)
            self.mp_state.set("开" if on else "关")
            self._parse_line(e.text)
            if self.mp_expect is not None:
                self.status.set(
                    "畅连 校验通过" if on == self.mp_expect
                    else f"畅连 未确认：期望 {self.mp_expect}，读回 {on}")
                self.mp_expect = None
        elif e.kind == "tone":
            payload = e.payload or {}
            val = payload.get("value")
            label = payload.get("label", e.text)
            self.tone_value = val
            self.tone_var.set(str(label))
            self._parse_line(e.text)
            if self.tone_expect is not None:
                self.status.set(
                    "提示音量 校验通过" if val == self.tone_expect
                    else f"提示音量 未确认：期望 {self.tone_expect}，读回 {val}")
                self.tone_expect = None
        elif e.kind == "ldac":
            on = bool((e.payload or {}).get("on"))
            self.ldac_value = on
            self.ldac_on.set(on)
            self.ldac_state.set("开" if on else "关")
            self._parse_line(e.text)
            if self.ldac_expect is not None:
                self.status.set(
                    "LDAC 校验通过" if on == self.ldac_expect
                    else f"LDAC 未确认：期望 {self.ldac_expect}，读回 {on}")
                self.ldac_expect = None
        elif e.kind == "ldac_unlock":
            unlocked = bool((e.payload or {}).get("unlocked"))
            self.ldac_unlocked = unlocked
            self._parse_line(e.text)
            self._apply_ldac_lock()
        elif e.kind == "setack":
            payload = e.payload or {}
            ok = bool(payload.get("ok"))
            name = payload.get("name", "")
            # 被拒绝时给出可操作的解释（低延迟 2/3 档在 Mate 机型上是预期行为）
            if not ok and "低延迟" in name:
                self.status.set(
                    "低延迟：耳机拒绝了这一档 —— Mate 机型（Key Max）只支持「关闭 / 开启」两档，"
                    "音乐/视频档需要 GAIA 机型")
            elif not ok:
                self.status.set(f"{name} 被耳机拒绝")
            self._parse_line(e.text)

    def _append(self, tag: str, clock: str, text: str) -> None:
        """写一条日志：先进缓冲（窗口关着也不丢），再同步给设置窗口的日志页。"""
        direction = "TX" if tag == "tx" else ("RX" if tag == "rx" else "")
        self.log_store.add(tag, clock, text, direction=direction)
        win = self.settings_window
        if win is not None and getattr(win, "win", None) is not None:
            win.append_log(tag, clock, text)

    def _parse_line(self, text: str) -> None:
        self._append("parse", f"{datetime.now():%H:%M:%S}", text)

    # ------------------------------------------------------------- 动作回调
    def _rename_current_device(self, name: str) -> None:
        """连上后从耳机读到真实型号时，把下拉框里那一项就地改名。

        BLE 广播常不带名字，扫描列表只能显示 MAC / 「FIIL 设备（型号待连接后读取）」。
        连上后 GET_DEVICE_NAME 会返回真实名（如 "FIIL Key Max"），此时把列表项改掉，
        下次同一地址再扫描也能立刻认出来（配合配对缓存）。"""
        try:
            idx = self.cmb_dev.current()
            if idx < 0 or idx >= len(self.scan_results):
                return
            row = list(self.scan_results[idx])
            addr = row[0]
            row[1] = name
            # 第 5 列 = 是否 FIIL；读到真名即算命中
            if len(row) > 4:
                row[4] = True
            else:
                row.append(True)
            self.scan_results[idx] = tuple(row)
            rssi = row[2]
            rssi_txt = f"RSSI {rssi}" if rssi is not None else "RSSI ?"
            items = list(self.cmb_dev["values"])
            items[idx] = f"★ {name}  [{addr}]  {rssi_txt}"
            self.cmb_dev["values"] = items
            self.cmb_dev.current(idx)
        except Exception:  # noqa: BLE001 - 改名失败不影响使用
            pass

    def _on_scan(self) -> None:
        self.status.set("扫描中（8 秒）…")
        self.worker.submit(Command("scan"))

    def _on_connect_addr(self) -> None:
        """按手填/记住的 BLE 地址直连（耳机不广播时的唯一入口）。"""
        addr = (self.var_addr.get() or "").strip().upper()
        if not addr:
            messagebox.showwarning("缺少地址", "请先填写耳机的 BLE 地址，例如 B0:F1:A3:00:00:01。")
            return
        # 规范化：允许用户粘 "b0f1a3000001" / "b0-f1-a3-00-00-01" 这类写法
        raw = "".join(ch for ch in addr if ch.isalnum())
        if len(raw) != 12 or any(c not in "0123456789ABCDEF" for c in raw):
            messagebox.showwarning("地址格式不对",
                                   "BLE 地址应为 12 位十六进制，如 B0:F1:A3:00:00:01。")
            return
        addr = ":".join(raw[i:i + 2] for i in range(0, 12, 2))
        self.worker.submit(Command("connect", addr))
        self.status.set(f"正在连接 {addr} …")

    def _on_connect(self) -> None:
        idx = self.cmb_dev.current()
        if idx < 0 or idx >= len(self.scan_results):
            messagebox.showwarning("未选择设备", "请先扫描并选择一个设备。")
            return
        addr = self.scan_results[idx][0]
        self.worker.submit(Command("connect", addr))
        self.status.set(f"正在连接 {addr} …")

    def _on_disconnect(self) -> None:
        self.worker.submit(Command("disconnect"))

    def _on_refresh_all(self) -> None:
        if not self.connected:
            messagebox.showwarning("未连接", "请先连接耳机。")
            return
        self.worker.submit(Command("call", ("request_battery", "GET_BATTERY")))
        self.root.after(120, lambda: self.worker.submit(Command("call", ("request_version", "GET_VERSION"))))
        self.root.after(240, lambda: self.worker.submit(Command("call", ("request_device_name", "GET_DEVICE_NAME"))))
        self.root.after(360, lambda: self.worker.submit(Command("call", ("request_maf", "GET_MAF"))))
        self.root.after(480, lambda: self.worker.submit(Command("call", ("request_cinema", "GET_SPATIAL"))))
        self.root.after(600, lambda: self.worker.submit(Command("call", ("request_low_latency", "GET_LOW_LATENCY"))))
        self.root.after(720, lambda: self.worker.submit(Command("call", ("request_multipoint", "GET_MULTIPOINT"))))
        self.root.after(840, lambda: self.worker.submit(Command("call", ("request_tone_volume", "GET_TONE"))))
        self.root.after(960, lambda: self.worker.submit(Command("call", ("request_ldac_unlock", "GET_LDAC_UNLOCK"))))
        self.root.after(1080, lambda: self.worker.submit(Command("call", ("request_ldac_switch", "GET_LDAC_SWITCH"))))
        self.status.set("已请求全部状态（依次读取）")

    def _send_preset(self, name: str, builder: str) -> None:
        self.worker.submit(Command("call", (builder, name)))

    def _on_set_maf(self, mode: MafMode) -> None:
        if not self.connected:
            messagebox.showwarning("未连接", "请先连接耳机。")
            return
        self.maf_expect = int(mode)
        self.worker.submit(Command("set", ("set_maf", mode, f"SET_MAF={int(mode)}({mode.label_cn})")))
        self.status.set(f"已下发 MAF={mode.label_cn}，等待读回校验…")
        # MAF 实测生效延迟 1.75~2.0s（PROTOCOL_PC.md §6.5）：
        # 600ms 读回必然是旧值，所以只做「快照」；真正的判定放在 2.2s。
        self.root.after(600, lambda: self._send_preset("GET_MAF(600ms 快照)", "request_maf"))
        self.root.after(2200, lambda: self._send_preset("GET_MAF(2.2s 复核)", "request_maf"))

    def _on_set_cinema(self) -> None:
        if not self.connected:
            messagebox.showwarning("未连接", "请先连接耳机。")
            self.cinema_on.set(not self.cinema_on.get())  # 回滚 UI
            return
        on = self.cinema_on.get()
        self.cinema_expect = on
        self.worker.submit(Command("set", ("set_cinema", on, f"SET_SPATIAL={'1' if on else '0'}")))
        self.status.set(f"已下发影院={'开' if on else '关'}，等待读回校验…")
        # 影院实测 ≤0.3s 即生效（PROTOCOL_PC.md §6.6）
        self.root.after(500, lambda: self._send_preset("GET_SPATIAL(校验)", "request_cinema"))

    # -- 低延迟 ---------------------------------------------------------------
    def _ensure_ll_buttons(self, val: int | None) -> None:
        """读到 2/3 说明这是 GAIA 四档机型，动态补出「音乐 / 视频」两个按钮。

        Mate 机型（Key Max 等）只有 0/1 两档，SET 2/3 会被耳机在 ACK 里拒绝，
        所以默认不显示，免得用户以为按钮坏了。
        """
        if val is None or val in (0, 1):
            return
        row = self.ll_btns[0].master
        for v, label in ((2, "音乐"), (3, "视频")):
            if v not in self.ll_btns:
                b = ttk.Button(row, text=label, width=9,
                               command=lambda vv=v: self._on_set_low_latency(vv))
                b.pack(side=tk.LEFT, padx=2, before=self.ll_btns[1])
                self.ll_btns[v] = b

    def _on_set_low_latency(self, level: int) -> None:
        if not self.connected:
            messagebox.showwarning("未连接", "请先连接耳机。")
            return
        self.ll_expect = level
        label = {0: "关闭", 1: "开启", 2: "音乐", 3: "视频"}.get(level, str(level))
        self.worker.submit(Command("set", ("set_low_latency", level,
                                           f"SET_LOW_LATENCY={level}({label})")))
        self.status.set(f"已下发低延迟={label}，等待读回校验…")
        # 低延迟实测 0.3s 内生效（set_matrix 实验），1.2s 足够
        self.root.after(1200, lambda: self._send_preset("GET_LOW_LATENCY(校验)",
                                                        "request_low_latency"))

    # -- 蓝牙畅连 -------------------------------------------------------------
    def _on_set_multipoint(self) -> None:
        if not self.connected:
            messagebox.showwarning("未连接", "请先连接耳机。")
            self.mp_on.set(not self.mp_on.get())
            return
        on = self.mp_on.get()
        self.mp_expect = on
        self.worker.submit(Command("set", ("set_multipoint", on,
                                           f"SET_MULTIPOINT={'1' if on else '0'}")))
        self.status.set(f"已下发畅连={'开' if on else '关'} —— 耳机将切换主从角色并主动断开 BLE，"
                        "程序会自动等待并重连（约 10~20 秒），请勿手动操作。")
        # 畅连切换后 BLE 必断，1.5s 后的读回一定落到「未连接」。校验改在自动重连
        # 成功后由握手重读完成（见 ble_worker._reconnect_loop），这里不再定时发 GET。

    # -- LDAC 高清解码 ---------------------------------------------------------
    def _apply_ldac_lock(self) -> None:
        """按激活态启用/禁用 LDAC 开关，并把原因写清楚。

        未激活时耳机不接受开关（协议层也没有可离线完成的激活手段），
        所以这里禁用控件而不是让用户点了之后看到「被耳机拒绝」。
        """
        unlocked = self.ldac_unlocked
        if unlocked is None:
            self.ldac_lock_var.set("")
            state = "disabled"
        elif unlocked:
            self.ldac_lock_var.set("")
            state = "normal"
        else:
            self.ldac_lock_var.set(
                "⚠ 未激活：请先在手机 FIIL+ App 中完成 LDAC 激活（激活码由 FIIL 服务器下发，"
                "本程序无法代办）。激活后回到这里点「刷新全部状态」即可开关。")
            state = "disabled"
        try:
            self.chk_ldac.configure(state=state)
        except Exception:  # noqa: BLE001 - 控件已销毁（窗口正在关闭）
            pass

    def _on_set_ldac(self) -> None:
        if not self.connected:
            messagebox.showwarning("未连接", "请先连接耳机。")
            self.ldac_on.set(not self.ldac_on.get())
            return
        if not self.ldac_unlocked:
            messagebox.showwarning(
                "LDAC 未激活",
                "LDAC 需要先在手机 FIIL+ App 中激活（激活码由 FIIL 服务器下发，PC 端离线无法代办）。"
                "激活后回到本程序点「刷新全部状态」即可直接开关。",
                parent=self.root)
            self.ldac_on.set(bool(self.ldac_value))
            return
        on = self.ldac_on.get()
        self.ldac_expect = on
        if on:
            # 官方 App 在开 LDAC 前先关低延迟：两者互斥，不关会互相顶掉。
            self.ll_expect = 0
            self.worker.submit(Command("set", ("set_low_latency", 0, "SET_LOW_LATENCY=0(开LDAC前)")))
            self.status.set("正在关闭低延迟（LDAC 与低延迟互斥），随后开启 LDAC…")
            self.root.after(400, lambda: self._submit_ldac(True))
        else:
            self._submit_ldac(False)

    def _submit_ldac(self, on: bool) -> None:
        self.worker.submit(Command("set", ("set_ldac_switch", on,
                                           f"SET_LDAC={'1' if on else '0'}")))
        self.status.set(f"已下发 LDAC={'开' if on else '关'}，等待读回校验…"
                        "（切换后耳机可能短暂重连）")
        # 开关生效 + 可能的链路抖动，留足 2s 再读回
        self.root.after(2000, lambda: self._send_preset("GET_LDAC_SWITCH(校验)",
                                                        "request_ldac_switch"))

    # -- 语音提示音量 ---------------------------------------------------------
    def _on_set_tone(self, level: int) -> None:
        if not self.connected:
            messagebox.showwarning("未连接", "请先连接耳机。")
            return
        self.tone_expect = level
        label = {0: "高", 1: "中", 2: "低", 3: "静音"}.get(level, str(level))
        self.worker.submit(Command("set", ("set_tone_volume", level,
                                           f"SET_TONE={level}({label})")))
        self.status.set(f"已下发提示音量={label}，等待读回校验…")
        self.root.after(800, lambda: self._send_preset("GET_TONE(校验)",
                                                       "request_tone_volume"))

    def _fill_hex(self, hx: str) -> None:
        self.ent_hex.delete(0, tk.END)
        self.ent_hex.insert(0, hx)

    def _on_send_hex(self) -> None:
        if not self.connected:
            messagebox.showwarning("未连接", "请先连接耳机。")
            return
        hx = self.ent_hex.get().strip()
        self.worker.submit(Command("send_hex", hx, "原始 Hex"))

    def _on_listen(self) -> None:
        if not self.connected:
            messagebox.showwarning("未连接", "请先连接耳机。")
            return
        try:
            secs = float(self.listen_secs.get())
        except ValueError:
            secs = 60.0
        self.worker.submit(Command("listen", secs))
        messagebox.showinfo(
            "监听已开始",
            f"正在记录 {secs:.0f} 秒内的全部 notify。\n\n"
            "如果一直没有数据，请先在耳机上做一次操作（切换降噪 / 摘下耳机 / 调整音量），"
            "或者用上方按钮发一条查询命令触发上报。",
        )

    # ------------------------------------------------------------- 日志导出
    # 复制/导出/清空现在都在设置窗口的「日志」页上（v1.2.0 把日志整体搬过去），
    # 主窗口不再持有 Text 控件，所以这里不再有导出动作。
    def _copy_log(self) -> None:
        """兼容旧调用：转发给设置窗口的日志页。"""
        win = self.settings_window
        if win is not None and getattr(win, "win", None) is not None:
            win.copy_log()
            self.status.set("已复制全部日志到剪贴板")
        else:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.log_store.as_text())
            self.status.set("已复制全部日志到剪贴板")

    def _export_txt(self) -> None:
        win = self.settings_window
        if win is not None and getattr(win, "win", None) is not None:
            win.export_txt(OUT_DIR)
        else:
            self._open_settings_and_hint("日志页可以导出 TXT")

    def _export_csv(self) -> None:
        win = self.settings_window
        if win is not None and getattr(win, "win", None) is not None:
            win.export_csv(OUT_DIR)
        else:
            self._open_settings_and_hint("日志页可以导出 CSV")

    def _clear_log(self) -> None:
        self.log_store.clear()
        win = self.settings_window
        if win is not None and getattr(win, "win", None) is not None:
            win.sync_from_store()
        self.status.set("日志已清空")

    def _open_settings_and_hint(self, what: str) -> None:
        self.open_settings("日志")
        self.status.set(what)


def main() -> None:
    # 高分屏下不开 DPI 感知，tkinter 界面会被系统位图拉伸而发虚
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001 - 老系统或非 Windows 直接跳过
        pass

    root = tk.Tk()
    try:
        style = ttk.Style()
        style.theme_use("vista")
    except tk.TclError:
        pass
    icon = Path(__file__).resolve().parent / "build" / "app.ico"
    if getattr(sys, "frozen", False):
        icon = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "app.ico"
    try:
        if icon.exists():
            root.iconbitmap(default=str(icon))
    except Exception:  # noqa: BLE001 - 没有图标也要能启动
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
