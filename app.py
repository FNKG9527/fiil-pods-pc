"""FiilPods PC 探针 —— FIIL 耳机 BLE GATT 逆向与控制台（tkinter + Bleak）

运行：
    python app.py

设计约束（来自任务书）：
  * PC 程序是「协议调试探针」，不是最终产品。
  * 每一行发送/接收的字节都要显示来源与响应，供 Android 模块复用。
  * 无法验证的控制功能只保留在「原始 Hex 控制台」，不进入语义化按钮。
"""

from __future__ import annotations

import csv
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from ble_worker import BleWorker, Command, Event
from fiil_protocol import MafMode
from quick_controls import ControlState, menu_model
from tray_support import TrayIcon, build_menu_items, load_tray_backend
from window_lifecycle import MainThreadDispatcher, WindowLifecycle

APP_TITLE = "FiilPods PC 探针 — FIIL 耳机 BLE 逆向控制台"


def _app_dir() -> Path:
    """可写数据目录：打包成 exe 后程序目录通常在 Program Files（只读），
    所以冻结运行时把 out/ 与设备表放到 %LOCALAPPDATA%\\FiilPodsPC；源码运行时仍用脚本目录。"""
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "FiilPodsPC"
    return Path(__file__).resolve().parent


OUT_DIR = _app_dir() / "out"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1280x820")
        self.root.minsize(1080, 700)

        self.evt_q: queue.Queue[Event] = queue.Queue()
        self.cmd_q: queue.Queue[Command] = queue.Queue()
        self.worker = BleWorker(self.evt_q, self.cmd_q)
        self.worker.start()

        # 日志缓冲（导出用）
        self.rows: list[dict[str, str]] = []
        self.connected = False
        self.scan_results: list[tuple[str, str, int | None, Any]] = []

        # 写后读回校验：记下期望值，收到回读时比对（None = 当前无待校验项）
        self.maf_expect: int | None = None
        self.cinema_expect: bool | None = None
        self.ll_expect: int | None = None
        self.mp_expect: bool | None = None
        self.tone_expect: int | None = None

        # 托盘菜单用的最后已知状态（None = 还没读到，不要编造默认值）
        self.maf_value: int | None = None
        self.ll_value: int | None = None
        self.cinema_value: bool | None = None
        self.mp_value: bool | None = None
        self.tone_value: int | None = None

        # 托盘与主线程调度：pystray 在自己的线程跑，所有 Tk 动作都排回主线程
        self._dispatch_q: queue.Queue = queue.Queue()
        self._dispatcher = MainThreadDispatcher(
            self._dispatch_q, threading.get_ident(),
            lambda exc: self._append("err", "", f"!! 托盘回调异常：{exc!r}"),
        )
        backend = load_tray_backend()
        self.tray = TrayIcon(backend, APP_TITLE, self._on_tray_action)

        self._build_ui()
        self._prefill_last_addr()
        self.lifecycle = WindowLifecycle(root, self.tray, self.worker)
        self._install_window_callbacks()

        # 托盘必须等界面建好后再启动：不可用时要往日志区写原因
        if backend.available and not self.tray.start(self._tray_items()):
            self._append("sys", "", "[托盘] 初始化失败，程序保持普通窗口模式。")
        elif not backend.available:
            self._append("sys", "", f"[托盘] 不可用：{backend.error}")

        self.root.after(60, self._pump)

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
                f"上次连接：{name or 'FIIL 耳机'} [{addr}] —— 可直接点「按地址连接」"
            )
        except Exception:  # noqa: BLE001 - 预填失败不影响启动
            pass

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}

        # ---- 顶部：连接条 ----
        top = ttk.LabelFrame(self.root, text="① 设备连接")
        top.pack(fill=tk.X, **pad)

        row = ttk.Frame(top)
        row.pack(fill=tk.X, padx=8, pady=6)

        ttk.Button(row, text="扫描设备", command=self._on_scan, width=12).pack(side=tk.LEFT)
        self.cmb_dev = ttk.Combobox(row, width=48, state="readonly")
        self.cmb_dev.pack(side=tk.LEFT, padx=8)
        self.btn_conn = ttk.Button(row, text="连接", command=self._on_connect, width=10)
        self.btn_conn.pack(side=tk.LEFT)
        ttk.Button(row, text="断开", command=self._on_disconnect, width=10).pack(side=tk.LEFT, padx=4)
        # 后台运行与真正退出：托盘不可用时也不能把用户困在「只能隐藏」的状态
        ttk.Button(row, text="后台运行", command=self.hide_to_tray,
                   width=10).pack(side=tk.LEFT, padx=(12, 2))
        ttk.Button(row, text="退出程序", command=self.request_exit,
                   width=10).pack(side=tk.LEFT, padx=2)

        self.lbl_state = ttk.Label(row, text="未连接", foreground="#c0392b")
        self.lbl_state.pack(side=tk.LEFT, padx=12)
        ttk.Label(row, text="状态：").pack(side=tk.LEFT)

        # ---- 直连地址（扫描不到时的逃生通道）----
        # 实测：FIIL 双模耳机被经典蓝牙占用时不再发 BLE 广播，扫描列表里根本没有它。
        # 但它仍然可以用 BLE 地址直连，所以留一个手填入口，不用等广播。
        row2 = ttk.Frame(top)
        row2.pack(fill=tk.X, padx=8, pady=(0, 6))
        ttk.Label(row2, text="直连地址：").pack(side=tk.LEFT)
        self.var_addr = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.var_addr, width=24).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row2, text="按地址连接", command=self._on_connect_addr,
                   width=12).pack(side=tk.LEFT)
        ttk.Label(
            row2,
            text="（耳机不出现在扫描列表时用；上次连过的地址会自动记住）",
            foreground="#666666",
        ).pack(side=tk.LEFT, padx=8)

        # ---- 主体：左控制 / 右日志 ----
        body = ttk.Frame(self.root)
        body.pack(fill=tk.BOTH, expand=True, **pad)

        left = ttk.Frame(body, width=460)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        left.pack_propagate(False)

        right = ttk.Frame(body)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_left(left)
        self._build_right(right)

        # ---- 底部状态栏 ----
        self.status = tk.StringVar(value="就绪。先点「扫描设备」，选中 FIIL 耳机后连接。")
        bar = ttk.Label(self.root, textvariable=self.status, relief=tk.SUNKEN, anchor=tk.W)
        bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _build_left(self, parent: ttk.Frame) -> None:
        # ---- ② 电量 ----
        box_bat = ttk.LabelFrame(parent, text="② 电量（实机读回）")
        box_bat.pack(fill=tk.X, pady=(0, 6))

        grid = ttk.Frame(box_bat)
        grid.pack(fill=tk.X, padx=8, pady=6)

        self.bat_vars = {}
        self.bat_labels = {}
        for i, (key, label) in enumerate((("left", "左耳"), ("right", "右耳"), ("case", "充电盒"))):
            lbl = ttk.Label(grid, text=label, width=7)
            lbl.grid(row=0, column=i * 2, sticky=tk.W)
            self.bat_labels[key] = lbl
            v = tk.StringVar(value="--")
            self.bat_vars[key] = v
            ttk.Label(grid, textvariable=v, font=("Segoe UI", 20, "bold"), width=7,
                      foreground="#2c7a2c").grid(row=0, column=i * 2 + 1, sticky=tk.W)

        ttk.Button(box_bat, text="刷新电量 / 全部状态", command=self._on_refresh_all).pack(
            fill=tk.X, padx=8, pady=(0, 8))

        # ---- ③ MAF ----
        box_maf = ttk.LabelFrame(parent, text="③ 降噪模式 MAF（写后读回确认）")
        box_maf.pack(fill=tk.X, pady=(0, 6))

        self.maf_var = tk.StringVar(value="--")
        wrap = ttk.Frame(box_maf)
        wrap.pack(fill=tk.X, padx=8, pady=6)

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
        btn_row = ttk.Frame(wrap)
        btn_row.pack(fill=tk.X)
        for m, label in modes:
            ttk.Button(btn_row, text=label, width=7,
                       command=lambda mm=m: self._on_set_maf(mm)).pack(side=tk.LEFT, padx=1)

        ttk.Label(wrap, text="当前读回：").pack(side=tk.LEFT, padx=(10, 2))
        ttk.Label(wrap, textvariable=self.maf_var, font=("Segoe UI", 11, "bold"),
                  foreground="#1a5fb4").pack(side=tk.LEFT)

        ttk.Label(box_maf, foreground="#8a5a00", wraplength=520, justify=tk.LEFT,
                  text="注：五档全部实机验证通过（SET 0→4 逐档 ACK 成功、读回一致）。"
                       "模式切换约 1.75~2.0s 才生效，本程序在 2.2s 后读回判定，"
                       "此前显示「未确认」属正常。").pack(
                          fill=tk.X, padx=8, pady=(0, 8))

        # ---- ④ 影院 ----
        box_cin = ttk.LabelFrame(parent, text="④ 影院模式（空间音频）")
        box_cin.pack(fill=tk.X, pady=(0, 6))
        crow = ttk.Frame(box_cin)
        crow.pack(fill=tk.X, padx=8, pady=8)

        self.cinema_on = tk.BooleanVar(value=False)
        self.chk_cinema = ttk.Checkbutton(crow, text="开启影院模式", variable=self.cinema_on,
                                          command=self._on_set_cinema)
        self.chk_cinema.pack(side=tk.LEFT)
        self.cinema_state = tk.StringVar(value="--")
        ttk.Label(crow, text="读回：").pack(side=tk.LEFT, padx=(16, 2))
        ttk.Label(crow, textvariable=self.cinema_state, font=("Segoe UI", 11, "bold"),
                  foreground="#1a5fb4").pack(side=tk.LEFT)

        # ---- ⑤ 低延迟 ----
        box_ll = ttk.LabelFrame(parent, text="⑤ 低延迟模式")
        box_ll.pack(fill=tk.X, pady=(0, 6))
        lrow = ttk.Frame(box_ll)
        lrow.pack(fill=tk.X, padx=8, pady=(8, 2))

        self.ll_var = tk.StringVar(value="--")
        self.ll_btns: dict[int, ttk.Button] = {}
        # Mate 机型（含本机 Key Max）只有 0=关闭 / 1=开启两档 —— 实测 SET 2/3 被耳机拒绝。
        # 按钮先按 Mate 两档建；若读回出现 2/3（说明是 GAIA 机型）再动态补上音乐/视频。
        for val, label in ((0, "关闭"), (1, "开启（游戏）")):
            b = ttk.Button(lrow, text=label, width=13,
                           command=lambda v=val: self._on_set_low_latency(v))
            b.pack(side=tk.LEFT, padx=2)
            self.ll_btns[val] = b
        ttk.Label(lrow, text="读回：").pack(side=tk.LEFT, padx=(14, 2))
        ttk.Label(lrow, textvariable=self.ll_var, font=("Segoe UI", 11, "bold"),
                  foreground="#1a5fb4").pack(side=tk.LEFT)

        ttk.Label(box_ll, foreground="#8a5a00", wraplength=430, justify=tk.LEFT,
                  text="机型说明：Mate 机型（Key Max 等）只支持「关闭 / 开启」两档，"
                       "下发 音乐/视频 时耳机会在 ACK 里明确拒绝（payload=01），不是延迟没生效。"
                       "GAIA 老机型才有四档，读到 2/3 时会自动补出「音乐 / 视频」按钮。").pack(
                          fill=tk.X, padx=8, pady=(2, 8))

        # ---- ⑥ 蓝牙畅连 + 语音提示音量 ----
        box_mp = ttk.LabelFrame(parent, text="⑥ 蓝牙畅连（多点连接）与语音提示音量")
        box_mp.pack(fill=tk.X, pady=(0, 6))

        mrow = ttk.Frame(box_mp)
        mrow.pack(fill=tk.X, padx=8, pady=(8, 2))
        self.mp_on = tk.BooleanVar(value=False)
        self.chk_mp = ttk.Checkbutton(mrow, text="开启蓝牙畅连（同时连两台设备）",
                                      variable=self.mp_on, command=self._on_set_multipoint)
        self.chk_mp.pack(side=tk.LEFT)
        self.mp_state = tk.StringVar(value="--")
        ttk.Label(mrow, text="读回：").pack(side=tk.LEFT, padx=(14, 2))
        ttk.Label(mrow, textvariable=self.mp_state, font=("Segoe UI", 11, "bold"),
                  foreground="#1a5fb4").pack(side=tk.LEFT)

        trow = ttk.Frame(box_mp)
        trow.pack(fill=tk.X, padx=8, pady=(6, 2))
        ttk.Label(trow, text="语音提示音量：").pack(side=tk.LEFT)
        self.tone_var = tk.StringVar(value="--")
        self.tone_btns: dict[int, ttk.Button] = {}
        # 线值反序：0=高 / 1=中 / 2=低 / 3=静音（FIIL 原文 f43_select_operate_volume）
        for val, label in ((0, "音量高"), (1, "音量中"), (2, "音量低"), (3, "静音")):
            b = ttk.Button(trow, text=label, width=9,
                           command=lambda v=val: self._on_set_tone(v))
            b.pack(side=tk.LEFT, padx=2)
            self.tone_btns[val] = b
        ttk.Label(trow, text="读回：").pack(side=tk.LEFT, padx=(14, 2))
        ttk.Label(trow, textvariable=self.tone_var, font=("Segoe UI", 11, "bold"),
                  foreground="#1a5fb4").pack(side=tk.LEFT)

        ttk.Label(box_mp, foreground="#8a5a00", wraplength=430, justify=tk.LEFT,
                  text="⚠ 畅连切换（开或关）时，耳机固件会立刻断开 BLE 链路并互换主从角色。"
                       "这是耳机自身行为，无法避免；程序会自动等待并重连（约 10~20 秒），"
                       "重连成功后自动重读全部状态。").pack(fill=tk.X, padx=8, pady=(2, 8))

        # ---- ⑦ 原始 Hex 控制台入口 ----
        box_raw = ttk.LabelFrame(parent, text="⑦ 原始 Hex（直接手写字节发到耳机）")
        box_raw.pack(fill=tk.BOTH, expand=True)

        ttk.Label(box_raw, justify=tk.LEFT, wraplength=440, foreground="#444",
                  text="这就是 FIIL 耳机真正认的协议帧本身：一串十六进制字节，"
                       "按空格分开。上面所有按钮（降噪、影院、畅连…）最终都是拼出这样一串字节、"
                       "写到 ff17 特征值。这里给你直接手写的机会 —— 用来试我还没逆向出来的命令。"
                  ).pack(anchor=tk.W, padx=8, pady=(8, 2))
        ttk.Label(box_raw, justify=tk.LEFT, wraplength=440, foreground="#666", font=("Consolas", 9),
                  text="帧结构：[seq] [cid] [type] [reserved] [lenField] [cmdId] [payloadLen] [payload…]"
                  ).pack(anchor=tk.W, padx=8, pady=(0, 2))
        ttk.Label(box_raw, justify=tk.LEFT, wraplength=440, foreground="#888",
                  text="例：00 27 01 00 02 01 00 = 「查电量」—— seq=00 cid=0x27(查询) type=01(请求) "
                       "lenField=02 cmdId=01(电量) payloadLen=00（无参数）"
                  ).pack(anchor=tk.W, padx=8, pady=(0, 4))

        ttk.Label(box_raw, text="Hex（空格分隔）：").pack(anchor=tk.W, padx=8, pady=(4, 2))
        self.ent_hex = ttk.Entry(box_raw, font=("Consolas", 10))
        self.ent_hex.pack(fill=tk.X, padx=8)
        self.ent_hex.insert(0, "00 27 01 00 02 01 00")

        raw_btns = ttk.Frame(box_raw)
        raw_btns.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(raw_btns, text="发送到 ff17", command=self._on_send_hex).pack(side=tk.LEFT)
        ttk.Button(raw_btns, text="填入 GET_BATTERY", width=18,
                   command=lambda: self._fill_hex("00 27 01 00 02 01 00")).pack(side=tk.LEFT, padx=4)
        ttk.Button(raw_btns, text="填入 GET_MAF", width=14,
                   command=lambda: self._fill_hex("00 27 01 00 02 0C 00")).pack(side=tk.LEFT)

        ttk.Label(box_raw, text="常见命令（点击填入）：", foreground="#555").pack(
            anchor=tk.W, padx=8, pady=(6, 0))
        quick = ttk.Frame(box_raw)
        quick.pack(fill=tk.X, padx=8, pady=2)
        presets = [
            ("setUnlock", "00 27 01 00 04 FF 02 02 34"),
            ("GET_VERSION", "00 27 01 00 02 02 00"),
            ("GET_EQ", "00 27 01 00 02 04 00"),
            ("SET_MAF=1", "00 2C 01 00 01 01"),
            ("SET_CI=1", "00 32 01 00 01 01"),
        ]
        for label, hx in presets:
            ttk.Button(quick, text=label, width=12,
                       command=lambda h=hx: self._fill_hex(h)).pack(side=tk.LEFT, padx=1)

        # ---- 静默监听 ----
        box_listen = ttk.LabelFrame(parent, text="⑧ 静默监听（逆向取证）")
        box_listen.pack(fill=tk.X, pady=(6, 0))
        lrow = ttk.Frame(box_listen)
        lrow.pack(fill=tk.X, padx=8, pady=6)

        self.listen_secs = tk.StringVar(value="60")
        ttk.Label(lrow, text="时长(秒)：").pack(side=tk.LEFT)
        ttk.Spinbox(lrow, from_=10, to=300, width=6, textvariable=self.listen_secs).pack(side=tk.LEFT)
        ttk.Button(lrow, text="开始监听", command=self._on_listen).pack(side=tk.LEFT, padx=6)
        ttk.Button(lrow, text="停止", command=lambda: self.worker.submit(Command("stop_listen"))).pack(side=tk.LEFT)

    def _build_right(self, parent: ttk.Frame) -> None:
        nb = ttk.Notebook(parent)
        nb.pack(fill=tk.BOTH, expand=True)

        # --- 日志页 ---
        tab1 = ttk.Frame(nb)
        nb.add(tab1, text="收发日志（原始字节）")

        tb1 = ttk.Frame(tab1)
        tb1.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(tb1, text="复制全部", command=self._copy_log).pack(side=tk.LEFT)
        ttk.Button(tb1, text="导出 TXT", command=self._export_txt).pack(side=tk.LEFT, padx=4)
        ttk.Button(tb1, text="导出 CSV", command=self._export_csv).pack(side=tk.LEFT)
        ttk.Button(tb1, text="清空", command=self._clear_log).pack(side=tk.LEFT, padx=4)
        self.chk_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(tb1, text="自动滚动", variable=self.chk_autoscroll).pack(side=tk.LEFT, padx=10)

        self.txt = tk.Text(tab1, font=("Consolas", 9), wrap=tk.NONE, undo=False)
        ys = ttk.Scrollbar(tab1, orient=tk.VERTICAL, command=self.txt.yview)
        xs = ttk.Scrollbar(tab1, orient=tk.HORIZONTAL, command=self.txt.xview)
        self.txt.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        xs.pack(side=tk.BOTTOM, fill=tk.X)

        self.txt.tag_configure("tx", foreground="#0b5394")
        self.txt.tag_configure("rx", foreground="#2c7a2c")
        self.txt.tag_configure("err", foreground="#c0392b")
        self.txt.tag_configure("sys", foreground="#7f6000")

        # --- 解析页 ---
        tab2 = ttk.Frame(nb)
        nb.add(tab2, text="解析结果")
        self.txt_parse = tk.Text(tab2, font=("Segoe UI", 10), wrap=tk.WORD)
        ps = ttk.Scrollbar(tab2, orient=tk.VERTICAL, command=self.txt_parse.yview)
        self.txt_parse.configure(yscrollcommand=ps.set)
        self.txt_parse.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ps.pack(side=tk.RIGHT, fill=tk.Y)

    # -------------------------------------------------------------- 事件泵
    def _pump(self) -> None:
        self._dispatcher.pump()
        try:
            while True:
                evt = self.evt_q.get_nowait()
                self._handle_event(evt)
                if evt.kind in ("conn", "maf", "lowlatency", "cinema", "multipoint", "tone"):
                    self._refresh_tray_menu()
        except queue.Empty:
            pass
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

    # -------------------------------------------------------------- 窗口
    def _install_window_callbacks(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

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
        try:
            self._dispatcher.close()
        except Exception:  # noqa: BLE001
            pass
        self.lifecycle.exit()

    def _handle_event(self, e: Event) -> None:
        if e.kind == "log":
            self._append("sys", e.clock, e.text)
            # 连上后读到真实型号（GET_DEVICE_NAME）时，把扫描列表那一项就地改名，
            # 免得下次还要靠「误打误撞点 MAC」才找得到耳机。
            if e.text.startswith("设备名："):
                self._rename_current_device(e.text.split("：", 1)[1].strip())
        elif e.kind == "tx":
            self._append("tx", e.clock, "TX  " + e.text)
            self.rows.append({"time": e.clock, "dir": "TX", "data": e.text, "note": ""})
        elif e.kind == "rx":
            self._append("rx", e.clock, "RX  " + e.text)
            self.rows.append({"time": e.clock, "dir": "RX", "data": e.text, "note": "notify"})
        elif e.kind == "error":
            self._append("err", e.clock, "!!  " + e.text)
            self.status.set(e.text.splitlines()[0][:160])
        elif e.kind == "state":
            self.lbl_state.configure(text=e.text)
        elif e.kind == "conn":
            self.connected = bool((e.payload or {}).get("connected"))
            switching = "切换中" in (e.text or "")
            self.lbl_state.configure(
                text=e.text,
                foreground=("#8a5a00" if switching
                            else "#2c7a2c" if self.connected else "#c0392b"),
            )
            if not self.connected and not switching:
                # 真正的掉线才清空读数；畅连主从互换是预期行为，界面保留旧值避免闪烁
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
            if b.single is not None:
                # 单电池机型（Key Max / Aviator 等 F042·F046 族）：只有一个电池，
                # 官方源码 F046HomeFragment.setElectricity 只取 payload[1]。
                # 不能摊成「左 -- / 右 xx% / 盒 --」，那是三耳机型的语义。
                self.bat_labels["left"].configure(text="耳机电量")
                self.bat_vars["left"].set(f"{b.single}%")
                for key in ("right", "case"):
                    self.bat_labels[key].configure(text="")
                    self.bat_vars[key].set("")
            else:
                self.bat_labels["left"].configure(text="左耳")
                self.bat_labels["right"].configure(text="右耳")
                self.bat_labels["case"].configure(text="充电盒")
                self.bat_vars["left"].set("--" if b.left is None else f"{b.left}%")
                self.bat_vars["right"].set("--" if b.right is None else f"{b.right}%")
                self.bat_vars["case"].set("--" if b.case is None else f"{b.case}%")
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
        self.txt.insert(tk.END, f"{clock}  {text}\n", tag)
        if self.chk_autoscroll.get():
            self.txt.see(tk.END)

    def _parse_line(self, text: str) -> None:
        self.txt_parse.insert(tk.END, f"{datetime.now():%H:%M:%S}  {text}\n")
        self.txt_parse.see(tk.END)

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
    def _copy_log(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.txt.get("1.0", tk.END))
        self.status.set("已复制全部日志到剪贴板")

    def _export_txt(self) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(
            initialdir=str(OUT_DIR),
            initialfile=f"fiil_log_{datetime.now():%Y%m%d_%H%M%S}.txt",
            defaultextension=".txt",
            filetypes=[("文本", "*.txt"), ("全部", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(self.txt.get("1.0", tk.END), encoding="utf-8")
        self.status.set(f"已导出 {path}")

    def _export_csv(self) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(
            initialdir=str(OUT_DIR),
            initialfile=f"fiil_log_{datetime.now():%Y%m%d_%H%M%S}.csv",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("全部", "*.*")],
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=["time", "dir", "data", "note"])
            w.writeheader()
            w.writerows(self.rows)
        self.status.set(f"已导出 {path}（{len(self.rows)} 行）")

    def _clear_log(self) -> None:
        self.txt.delete("1.0", tk.END)
        self.txt_parse.delete("1.0", tk.END)
        self.rows.clear()


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
