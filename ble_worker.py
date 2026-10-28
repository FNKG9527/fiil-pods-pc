"""Bleak 异步工作线程 + 事件队列桥（GUI 与 BLE 事件循环分离）

设计要点
--------
* GUI（tkinter）在主线程；Bleak 是 asyncio，必须跑在**自己的线程 + 独立事件循环**里。
* 两个方向都用 `queue.Queue` 通信：
    - GUI → worker：命令队列 `cmd_q`
    - worker → GUI：事件队列 `evt_q`（连接状态、收发帧、扫描结果、错误）
* tkinter 侧用 `root.after(50, pump)` 轮询 `evt_q`，绝不在 GUI 线程里 await。
* 所有 BLE 调用都包 try/except + 超时；断开时 stop_notify + disconnect + 状态复位。
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from ble_connect import DeviceCache, connect_fast
from fiil_protocol import (
    CHAR_NOTIFY_UUID,
    CHAR_WRITE_UUID,
    FiilProtocol,
    Frame,
)

# ----------------------------------------------------------------------------
# 设备识别（扫描时用）
# ----------------------------------------------------------------------------

# FIIL 的蓝牙 MAC OUI 前缀（逆向自 FIIL+ APP 的 FiilConfig.FIIL_OUI_PREFIXES）。
# 实测：FIIL 耳机在 BLE 广播里通常不带 local_name，只能靠 OUI 先认出来，
# 真正的型号要等连接后发 GET_DEVICE_NAME（cmdId=3）才知道。
FIIL_OUI_PREFIXES: tuple[str, ...] = ("B0:F1:A3", "FF:F1:A3")

# Windows 配对缓存查询的最小间隔（秒）：扫描按钮连点没必要每次起 powershell
_PAIRED_CACHE_TTL = 30.0
_paired_cache: tuple[float, dict[str, str]] = (0.0, {})


def _match_nearby_cached_name(address: str, cached: dict[str, str]) -> str:
    """双模设备的地址回退匹配。

    实测（本机 FIIL Key Max）：经典蓝牙 BD_ADDR 是 `B0:F1:A3:00:00:02`，
    而 BLE 广播地址是 `B0:F1:A3:00:00:01` —— 只有最后一个字节不同。
    所以先用前 5 段（OUI + 2 段）找同一台设备；命中唯一一条才采用，
    避免把同一 OUI 下不同型号的耳机名字张冠李戴。
    """
    addr = address.upper()
    prefix = addr[:14]  # "B0:F1:A3:00:00"
    hits = [v for k, v in cached.items() if k.upper().startswith(prefix)]
    return hits[0] if len(hits) == 1 else ""


# ----------------------------------------------------------------------------
# 已知设备表（耳机不广播时的直连兜底）
# ----------------------------------------------------------------------------
# 实测教训：FIIL 双模耳机在被经典蓝牙（A2DP）占用时**不再发 BLE 可连接广播**，
# 此时 BleakScanner 扫 100+ 个设备也一个 FIIL 都看不到，界面上就只剩一堆无名 MAC。
# 但它仍然可以被 BLE 直连（WinRT 用地址直接 connectGatt）。
# 所以每次连接成功就把「BLE 地址 → 型号」记到本地 json，扫描时把没扫到的补进列表。

_KNOWN_DEVICES_PATH = (
    (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "FiilPodsPC" / "known_devices.json")
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent / "known_devices.json"
)


def _seed_known_devices() -> None:
    """安装版首次运行：把打包时带上的设备表复制到用户数据目录，省得重扫。

    只做一次，用户目录里已有文件时不覆盖（否则会抹掉新学到的型号）。
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        if _KNOWN_DEVICES_PATH.exists():
            return
        bundled = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "known_devices.json"
        if bundled.exists():
            _KNOWN_DEVICES_PATH.parent.mkdir(parents=True, exist_ok=True)
            _KNOWN_DEVICES_PATH.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:  # noqa: BLE001 - 播种失败只是少了个便利功能
        pass


_seed_known_devices()


def _load_known_devices() -> dict[str, dict[str, Any]]:
    """读本地已知设备表（连接成功后写入）。任何异常都返回空表。"""
    try:
        raw = json.loads(_KNOWN_DEVICES_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k).upper(): v for k, v in raw.items() if isinstance(v, dict)}
    except Exception:  # noqa: BLE001 - 配置读失败绝不能影响扫描/连接
        pass
    return {}


def _save_known_device(address: str, name: str) -> None:
    """把「BLE 地址 → 型号」写进本地已知设备表。

    只记录真正连上过的地址：BLE 广播地址与经典蓝牙 BD_ADDR 未必相同
    （本机 FIIL Key Max 经典 …:00:02、BLE …:00:01），所以不能用配对缓存里的地址直连。
    """
    if not address:
        return
    try:
        table = _load_known_devices()
        key = address.upper()
        new_name = (name or "").strip()
        # 连接成功时会先写一条空名字（那时还没读到型号），随后 GET_DEVICE_NAME 才补真名。
        # 这里必须保留已有的名字，否则第二笔空写会把刚学到的型号抹掉。
        old_name = str(table.get(key, {}).get("name") or "").strip()
        table[key] = {
            "name": new_name or old_name,
            "last_connected": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _KNOWN_DEVICES_PATH.write_text(
            json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 - 记不下来也只是下次要重扫，不能影响连接
        pass


def _windows_paired_names() -> dict[str, str]:
    """查 Windows 已配对/历史蓝牙设备的「MAC → 名称」表。

    Bleak 在 Windows 上只从 BLE 广播/缓存拿 `dev.name`，而 FIIL 耳机是双模设备：
    BLE 广播常常是空名（主通道在经典蓝牙）。这里借 PnP 设备表把名字补回来。

    只读操作，不需要管理员权限；任何异常都静默返回空表（扫描不能因为查名字失败而失败）。
    """
    global _paired_cache
    now = time.time()
    if now - _paired_cache[0] < _PAIRED_CACHE_TTL:
        return _paired_cache[1]

    result: dict[str, str] = {}
    try:
        import subprocess

        ps = (
            "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
            "Where-Object { $_.FriendlyName -and $_.InstanceId -match 'DEV_([0-9A-F]{12})' } | "
            "ForEach-Object { "
            "$m = [regex]::Match($_.InstanceId,'DEV_([0-9A-F]{12})').Groups[1].Value; "
            "$mac = ($m -split '(.{2})' | Where-Object { $_ } ) -join ':'; "
            "'{0}|{1}' -f $mac,$_.FriendlyName }"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=12, check=False,
        )
        for line in (out.stdout or "").splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            mac, _, fname = line.partition("|")
            mac = mac.strip().upper()
            fname = fname.strip()
            if len(mac) == 17 and fname:
                result[mac] = fname
    except Exception:  # noqa: BLE001 - 查名字失败绝不能影响扫描
        result = {}

    _paired_cache = (now, result)
    return result

# ----------------------------------------------------------------------------
# 事件定义（worker → GUI）
# ----------------------------------------------------------------------------


@dataclass
class Event:
    kind: str                 # log|tx|rx|conn|scan|battery|maf|cinema|parse|state|error|done
    text: str = ""
    payload: Any = None
    ts: float = field(default_factory=time.time)

    @property
    def clock(self) -> str:
        return datetime.fromtimestamp(self.ts).strftime("%H:%M:%S.%f")[:-3]


# ----------------------------------------------------------------------------
# 命令定义（GUI → worker）
# ----------------------------------------------------------------------------


@dataclass
class Command:
    kind: str                 # scan | connect | disconnect | send_hex | send_bytes | call | listen
    payload: Any = None
    name: str = ""


# ----------------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------------


class BleWorker(threading.Thread):
    """BLE 工作线程。

    对外只暴露 `submit(Command)` 与 `events` 队列，线程内部自建 asyncio loop。
    """

    def __init__(self, evt_q: queue.Queue[Event], cmd_q: queue.Queue[Command]) -> None:
        super().__init__(daemon=True, name="FiilBleWorker")
        self.evt_q = evt_q
        self.cmd_q = cmd_q
        self.loop: asyncio.AbstractEventLoop | None = None
        self.client: BleakClient | None = None
        self.protocol = FiilProtocol()
        self.address: str = ""
        self.device_name: str = ""
        self._stop = threading.Event()
        self._pending: dict[str, asyncio.Future] = {}
        self._listen_task: asyncio.Task | None = None
        self._listening_since: float = 0.0
        self._listen_count: int = 0
        # 最近一次下发的 SET（用于把耳机 ACK 与请求对上；Mate 的 seq 不可用于匹配）
        self._pending_set_cid: int | None = None
        self._pending_set_name: str | None = None
        # 握手自检：这三个状态最容易因为帧发得太密而漏回包，记下有没有收到
        self._got_ll = False
        self._got_mp = False
        self._got_tone = False
        self._got_ldac = False
        self._got_ldac_unlock = False
        # True 表示当前断开是我们主动发起，用于抑制 disconnected_callback 的重复上报
        self._closing: bool = False
        # 畅连（多点）切换后耳机一定会主动踢掉 BLE 链路（主从角色互换）。
        # 实测三次切换全部在 ACK 后 3~46 毫秒断开，且断开后不会自己回来。
        # 这是固件行为，不是我们的 bug —— 唯一能做的是断线后自动重连并把状态重读一遍。
        self._reconnect_after_multipoint: bool = False
        self._reconnect_task: asyncio.Task | None = None
        # 扫到过的 BLEDevice 缓存：下次连同一地址可直接建连，跳过一轮完整扫描
        self._device_cache = DeviceCache()
        # 扫描时长/连接超时由设置页下发（默认值与 v1.1.0 一致）
        self.scan_timeout: float = 8.0
        self.connect_timeout: float = 20.0

    # -- 对外 ---------------------------------------------------------------
    def submit(self, cmd: Command) -> None:
        self.cmd_q.put(cmd)

    def shutdown(self) -> None:
        self._stop.set()
        self.cmd_q.put(Command("disconnect"))

    # -- 线程主体 -----------------------------------------------------------
    def run(self) -> None:  # noqa: D102
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except Exception:  # noqa: BLE001
            self._emit("error", traceback.format_exc())
        finally:
            try:
                loop.run_until_complete(self._close_client())
            except Exception:  # noqa: BLE001
                pass
            loop.close()

    async def _main(self) -> None:
        self._emit("state", "就绪")
        while not self._stop.is_set():
            try:
                cmd = await asyncio.to_thread(self.cmd_q.get, True, 0.2)
            except queue.Empty:
                continue
            try:
                await self._handle(cmd)
            except BleakError as e:
                self._emit("error", f"BLE 错误：{e}")
            except Exception as e:  # noqa: BLE001
                self._emit("error", f"{cmd.kind} 失败：{e}\n{traceback.format_exc()}")

    def _emit(self, kind: str, text: str = "", payload: Any = None) -> None:
        self.evt_q.put(Event(kind, text, payload))

    async def _handle(self, cmd: Command) -> None:
        if cmd.kind == "scan":
            await self._scan()
        elif cmd.kind == "connect":
            await self._connect(str(cmd.payload))
        elif cmd.kind == "disconnect":
            await self._close_client()
        elif cmd.kind == "send_hex":
            await self._send_hex(cmd.name, str(cmd.payload))
        elif cmd.kind == "send_bytes":
            await self._send_raw(cmd.name, bytes(cmd.payload))
        elif cmd.kind == "call":
            # 在 worker 线程里组帧，避免 GUI 线程与 worker 线程争抢 protocol._seq
            await self._call(cmd)
        elif cmd.kind == "set":
            # payload = (方法名, 参数, 显示名)
            method, arg, label = cmd.payload  # type: ignore[misc]
            builder = getattr(self.protocol, method, None)
            if builder is None:
                self._emit("error", f"协议层没有方法 {method}")
                return
            try:
                frame = builder(arg)
            except Exception as e:  # noqa: BLE001
                self._emit("error", f"{label} 组帧失败：{e}")
                return
            # 畅连（多点）切换，无论开还是关，耳机都会立刻踢掉 BLE 链路。
            # 必须在下发之前置标志，否则 disconnected_callback 快过任何事后设置。
            if method == "set_multipoint":
                self._reconnect_after_multipoint = True
            await self._send_raw(label, frame)
        elif cmd.kind == "listen":
            await self._start_listen(float(cmd.payload or 60))
        elif cmd.kind == "stop_listen":
            await self._stop_listen()
        elif cmd.kind == "config":
            # payload = (scan_timeout, connect_timeout)，由设置页实时下发
            scan_timeout, connect_timeout = cmd.payload  # type: ignore[misc]
            self.scan_timeout = float(scan_timeout)
            self.connect_timeout = float(connect_timeout)
        else:
            self._emit("error", f"未知命令 {cmd.kind}")

    async def _call(self, cmd: Command) -> None:
        """调用 FiilProtocol 上的无参方法组帧后发送。

        payload = (方法名, 显示名) 元组。
        """
        method, label = cmd.payload  # type: ignore[misc]
        builder = getattr(self.protocol, method, None)
        if builder is None:
            self._emit("error", f"协议层没有方法 {method}")
            return
        try:
            frame = builder()
        except Exception as e:  # noqa: BLE001
            self._emit("error", f"{label or method} 组帧失败：{e}")
            return
        await self._send_raw(label or method, frame)

    # -- 扫描 ---------------------------------------------------------------
    async def _scan(self) -> None:
        self._emit("state", "扫描中…")
        try:
            found = await BleakScanner.discover(timeout=self.scan_timeout, return_adv=True)
        except Exception as e:  # noqa: BLE001
            self._emit("error", f"扫描失败：{e}")
            return

        cached = _windows_paired_names()
        rows = []
        for dev, adv in found.values():
            address = dev.address
            self._device_cache.remember(dev)  # 扫描顺手缓存，下次连接可直接用
            # 名称的三个来源，按可信度排序（实测 FIIL 耳机在 BLE 广播里常常不带名字）：
            # ① 广播包里的 local_name / dev.name（最准，但 FIIL 通常没有）
            # ② Windows 蓝牙配对/历史缓存（WinRT 里查，连通用的经典蓝牙名字一起拿到）
            # ③ OUI 反推（FIIL 的 B0:F1:A3 / FF:F1:A3 前缀）——只知道是 FIIL，不知道型号
            name = (dev.name or adv.local_name or "").strip()
            if not name:
                name = cached.get(address.upper(), "")
            oui_hit = address.upper()[:8] in FIIL_OUI_PREFIXES
            if not name and oui_hit:
                # 双模设备实测：BLE 广播地址与经典蓝牙 BD_ADDR 可能只差最后一字节
                # （本机 FIIL Key Max 经典地址 …:00:02、BLE 地址 …:00:01）。
                # 所以先用前 5 段（OUI + 2 段）找同一个设备的缓存名，再退到占位文案。
                name = _match_nearby_cached_name(address, cached) or "FIIL 设备（型号待连接后读取）"
            rssi = getattr(adv, "rssi", None)
            rows.append((address, name, rssi, dev, oui_hit))
        # 已知设备兜底：连过的耳机如果此刻不广播（被经典蓝牙占用），扫描里根本没有它。
        # 把本地记录过的「BLE 地址 → 型号」补一行进去，用户仍可直接点连接。
        seen = {r[0].upper() for r in rows}
        for addr, info in _load_known_devices().items():
            if addr in seen:
                continue
            nm = str(info.get("name") or "").strip() or "FIIL 设备（已知地址）"
            if "FIIL" not in nm.upper() and addr[:8] not in FIIL_OUI_PREFIXES:
                continue
            rows.append((addr, nm, None, None, True))
            self._emit("log", f"[已知设备] {nm} [{addr}] 当前未广播，已加入列表可直接连接")
        # 排序：FIIL（名字命中或 OUI 命中）排前面，再按信号强度
        rows.sort(key=lambda r: (not (r[4] or "FIIL" in (r[1] or "").upper()), -(r[2] or -999)))
        self._emit("scan", f"发现 {len(rows)} 个设备", rows)
        self._emit("state", "就绪")

    # -- 连接 ---------------------------------------------------------------
    async def _connect(self, address: str) -> None:
        await self._close_client()
        self.address = address
        await self._connect_inner(address, keep_state=False)

    async def _connect_inner(self, address: str, keep_state: bool) -> bool:
        """建立连接 + 订阅 notify + 跑完整握手。

        keep_state=True 时用于「畅连切换后的自动重连」：不再发一遍
        「未连接」状态，也不调 _close_client（此时 client 已为 None）。
        """
        self.address = address
        self._emit("state", f"连接 {address} …")
        # 连接策略见 ble_connect：缓存直连 → 地址直连 → 边扫边连 → 地址兜底。
        # 关键收益：耳机此刻不广播时不再白等 20 秒扫描超时。
        # disconnected_callback 必须注册：否则耳机主动断线（走远/关机/被别的 App 抢走）
        # 时 Bleak 已断开但 GUI 仍显示「已连接」，后续每次发送都只是静默失败。
        result = await connect_fast(
            address,
            cache=self._device_cache,
            scan_timeout=self.scan_timeout,
            connect_timeout=self.connect_timeout,
            disconnected_callback=self._on_ble_disconnected,
            on_stage=lambda stage: self._emit("state", f"连接中：{stage}"),
        )
        client = result.client
        if client is None:
            self._emit("error", f"连接失败：{result.error}")
            self._emit("log", f"[!] 连接耗时明细：{result.summary()}")
            if result.reason == "not_advertising":
                # 最需要被看见的一种失败：耳机被经典蓝牙占着，BLE 根本不广播。
                # 胶囊位只放得下短句，可操作的指引交给状态栏和日志页。
                self._emit("state", "未连接 · 耳机不在广播")
                self._emit(
                    "log",
                    "[!] 提示：耳机此时正走经典蓝牙音频链路。Windows 会占用它，"
                    "导致 BLE 侧一片静默；请先在系统声音里断开该耳机或暂停播放，"
                    "等 3~5 秒后重试。",
                )
            if not keep_state:
                self._emit("conn", "未连接", {"connected": False})
                if result.reason != "not_advertising":
                    self._emit("state", "未连接")
            return False
        self._emit("log", f"[+] 连接路径：{result.stage}，{result.summary()}")

        self.client = client
        self.protocol = FiilProtocol()
        try:
            await client.start_notify(CHAR_NOTIFY_UUID, self._on_notify)
        except Exception as e:  # noqa: BLE001
            self._emit("error", f"订阅 notify 失败：{e}")
            self._closing = True
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._closing = False
            self.client = None
            return False

        self.device_name = ""
        self._emit("conn", "已连接", {"connected": True, "address": address})
        self._emit("state", "已连接")
        self._emit("log", f"[+] 已连接 {address}")
        # 连上就记住这个地址：下次耳机不广播时，扫描仍能从「已知设备」里把它捞出来
        # （FIIL 双模耳机被经典蓝牙占用时会停止 BLE 广播，这是实测到的常态）
        _save_known_device(address, "")

        # 连接初始化：setUnlock 是强制前置，不发耳机对所有查询静默
        await asyncio.sleep(0.4)
        await self._send_raw("setUnlock（连接握手）", self.protocol.request_unlock())
        await asyncio.sleep(0.5)
        # 握手后把关键状态全读一遍
        # 间隔 0.12s 太密：耳机侧对连续查询会丢包（实测握手阶段偶发 0/3 全丢）。
        # 0.25s 是实测稳定的下限；也不用更大，否则连接后要等太久才看到数据。
        self._got_ll = self._got_mp = self._got_tone = False
        self._got_ldac = self._got_ldac_unlock = False
        for name, frame in (
            ("GET_VERSION", self.protocol.request_version()),
            ("GET_DEVICE_NAME", self.protocol.request_device_name()),
            ("GET_BATTERY", self.protocol.request_battery()),
            ("GET_MAF", self.protocol.request_maf()),
            ("GET_SPATIAL（影院）", self.protocol.request_cinema()),
            ("GET_LOW_LATENCY", self.protocol.request_low_latency()),
            ("GET_MULTIPOINT", self.protocol.request_multipoint()),
            ("GET_TONE（语音提示音量）", self.protocol.request_tone_volume()),
            ("GET_LDAC_UNLOCK（LDAC 激活态）", self.protocol.request_ldac_unlock()),
            ("GET_LDAC_SWITCH（LDAC 开关）", self.protocol.request_ldac_switch()),
            ("GET_EQ", self.protocol.request_eq()),
        ):
            await self._send_raw(name, frame)
            await asyncio.sleep(0.25)
        # 握手自检：漏掉的关键状态补读一次（仍失败则留给用户点「刷新」）
        await asyncio.sleep(0.3)
        if not self._got_ll:
            await self._send_raw("GET_LOW_LATENCY（补读）", self.protocol.request_low_latency())
        if not self._got_mp:
            await self._send_raw("GET_MULTIPOINT（补读）", self.protocol.request_multipoint())
        if not self._got_tone:
            await self._send_raw("GET_TONE（补读）", self.protocol.request_tone_volume())
        if not self._got_ldac_unlock:
            await self._send_raw("GET_LDAC_UNLOCK（补读）", self.protocol.request_ldac_unlock())
        if not self._got_ldac:
            await self._send_raw("GET_LDAC_SWITCH（补读）", self.protocol.request_ldac_switch())
        return True

    def _on_ble_disconnected(self, _client: Any) -> None:
        """Bleak 的断线回调（在 worker 的 loop 线程里执行，不碰 tkinter）。"""
        # 主动 disconnect() 时 Bleak 也会回调，这里用 _closing 标志避免重复上报
        if self._closing:
            return
        if self._reconnect_after_multipoint:
            # 畅连切换触发的断开：不是故障，是耳机固件在换主从角色。
            # 不报「连接丢失」吓人，改为自动重连（实测需要 8~13 秒耳机才可再连）。
            self._reconnect_after_multipoint = False
            self._emit("conn", "切换中（畅连主从互换）", {"connected": False})
            self._emit("state", "畅连切换中，等待耳机重新可连…")
            self._emit("log", "[~] 畅连已生效，耳机正在切换主从角色 —— 等待其重新可连接后自动重连")
            if self.client is not None:
                try:
                    self.client = None
                except Exception:  # noqa: BLE001
                    pass
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())
            return
        self._emit("conn", "已断开（连接丢失）", {"connected": False})
        self._emit("state", "未连接")
        self._emit("log", "[!] 与耳机的连接已断开（耳机侧断开/超出范围/被其他 App 占用）")

    async def _reconnect_loop(self) -> None:
        """畅连切换后的自动重连：指数退避重试，成功后重跑完整握手。"""
        address = self.address
        delays = (2.0, 3.0, 5.0, 8.0, 12.0)
        for i, delay in enumerate(delays, 1):
            await asyncio.sleep(delay)
            if self._stop.is_set():
                return
            self._emit("log", f"[~] 第 {i}/{len(delays)} 次重连尝试（{address}）…")
            try:
                ok = await self._connect_inner(address, keep_state=True)
            except Exception as e:  # noqa: BLE001
                self._emit("log", f"[~] 重连异常：{e}")
                ok = False
            if ok:
                self._emit("log", "[+] 畅连切换后已自动重连，状态已重新读取")
                return
        self._emit("error", "畅连切换后自动重连失败：耳机可能仍在切换或已被其他设备占用。"
                           "请稍后手动点「连接」，或在耳机上按一下按键唤醒。")

    async def _close_client(self) -> None:
        # 用户主动断开时，必须取消正在跑的「畅连后自动重连」，否则会在断开后又自己连上
        self._reconnect_after_multipoint = False
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        self._closing = True
        if self.client is not None:
            try:
                if self.client.is_connected:
                    try:
                        await self.client.stop_notify(CHAR_NOTIFY_UUID)
                    except Exception:  # noqa: BLE001
                        pass
                    await self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self.client = None
        self._emit("conn", "未连接", {"connected": False})
        self._emit("state", "未连接")
        self._closing = False

    # -- 发送 ---------------------------------------------------------------
    async def _send_raw(self, name: str, frame: bytes) -> None:
        if self.client is None or not self.client.is_connected:
            self._emit("error", f"{name}：未连接，已丢弃")
            return
        hexs = " ".join(f"{b:02X}" for b in frame)
        # 记下这是哪条 SET 的帧（cid + 长度），用于把 ACK 和请求对上。
        # Mate 的 seq 不能用来匹配（耳机用自己的计数器，RESPONSE 与 NOTIFY 共用），
        # 所以只能靠 cid 语义 —— 这对 SET 足够，因为同一时刻只会下发一条。
        # 注意：GET 与 setUnlock 的 cid 也是 39，必须排除，否则会把查询当成待确认的 SET。
        # 也**不要**在非 SET 发送时清空 —— SET 之后常紧跟一条 GET 做校验，
        # 清空会让稍后到达的 ACK 失去归属。
        is_set = len(frame) >= 3 and frame[2] == 0x01 and frame[1] != 39
        if is_set:
            self._pending_set_cid = frame[1]
            self._pending_set_name = name
        try:
            await self.client.write_gatt_char(CHAR_WRITE_UUID, frame, response=False)
            self._emit("tx", f"{name:<22} {hexs}")
        except Exception as e:  # noqa: BLE001
            self._pending_set_cid = None
            self._pending_set_name = None
            self._emit("error", f"{name} 写入失败：{e}")

    async def _send_hex(self, name: str, text: str) -> None:
        cleaned = text.replace(",", " ").replace("0x", " ").replace("0X", " ")
        parts = cleaned.split()
        try:
            data = bytes(int(p, 16) & 0xFF for p in parts)
        except ValueError:
            self._emit("error", f"Hex 解析失败：{text!r}")
            return
        if not data:
            self._emit("error", "空的 Hex")
            return
        await self._send_raw(name or "原始 Hex", data)

    async def _send_set(self, cid: int, payload: bytes, label: str) -> None:
        """在 worker 线程里组 SET 帧（保证 seq 单调）并发送。"""
        frame = self.protocol._set(cid, payload)  # noqa: SLF001 - 同包协作
        await self._send_raw(label, frame)

    def _guess_ear_type(self) -> int | None:
        """从设备名推断 FIIL 型号号（earType）。

        本机型（Key Max）没有单字节 earType 查询命令 —— `GET_KEY(5)` 返回的是按键配置表
        （30 字节），不是型号。因此型号只能由 `GET_DEVICE_NAME`（cmdId=3，返回 ASCII
        "FIIL Key Max"）推断。规则与 Android 模块 `FiilModels.NAME_RULES` 保持一致。

        用途：BatteryState 需要知道是不是单电池机型（F042/F046 只读 payload[1]）。
        """
        name = (self.device_name or "").upper()
        if not name:
            return None
        for token, ear_type in (
            ("CC PRO3", 53), ("CC3", 51), ("KEY PRO2", 49), ("GS LINKS", 48),
            ("ATOM", 47), ("KEY MAX", 46), ("GS LITE", 45),
            ("CC PRO2", 39), ("CC NANO", 37),
            ("KEY PRO", 41), ("KEY", 38),
            ("T2 PRO", 35), ("T1 PRO", 27), ("T1 XS", 26), ("T1 LITE", 29),
            ("CG PRO", 33), ("BELT", 40), ("AVIATOR", 42),
        ):
            if token in name:
                return ear_type
        return None

    # -- 接收 ---------------------------------------------------------------
    def _on_notify(self, _char: Any, data: bytearray) -> None:
        chunk = bytes(data)
        frames = self.protocol.feed(chunk)
        for f in frames:
            self._emit_frame(f)

    def _emit_frame(self, f: Frame) -> None:
        # 静默监听期间统计条数（60s 静默监听的「共 N 条」就靠它）
        if self._listen_task is not None and not self._listen_task.done():
            self._listen_count += 1
        self._emit("rx", f"{f.describe():<34} {f.hex()}")
        # 语义化解析结果单独推给 GUI
        if f.cid == 39 and f.cmd is not None:
            self._interpret_query(f)
        elif f.is_notify:
            self._emit("log", f"[通知] cid={f.cid} cmd={f.cmd} payload={f.payload.hex(' ')}")
        elif f.is_response and f.cmd is None:
            # SET 命令的应答：6 字节短帧 `xx cc 02 00 01 <ok>`，无 cmdId。
            # payload[0] == 0 表示耳机接受了这条 SET，非 0 表示耳机拒绝。
            # 低延迟 2/3 档在 Mate 机型上就是靠这个字段拒绝的（payload=01）。
            self._interpret_set_ack(f)
        elif f.is_response and f.cmd is not None:
            # 带 cmdId 的 SET 应答（畅连 / 提示音音量走 cid=1 带 cmdId 那条路径）
            self._interpret_set_ack(f)

    def _interpret_set_ack(self, f: Frame) -> None:
        """把 SET 的 ACK 结果回报给 GUI，用于「下发成功/被耳机拒绝」的判定。"""
        if not f.payload:
            return
        ok = f.payload[-1] == 0
        name = self._pending_set_name or f"cid={f.cid}"
        self._pending_set_cid = None
        self._pending_set_name = None
        if ok:
            self._emit("log", f"{name}：耳机已接受")
            self._emit("setack", f"{name}：成功", {"ok": True, "name": name})
        else:
            # 典型场景：Mate 机型收到 SET_LL=2/3 → ACK payload=01（拒绝）
            self._emit("log", f"{name}：耳机拒绝（payload={f.payload.hex(' ')}）")
            self._emit("setack", f"{name}：被耳机拒绝", {"ok": False, "name": name})

    def _interpret_query(self, f: Frame) -> None:
        from fiil_protocol import (
            BatteryState,
            MafMode,
            parse_ascii,
            parse_bool,
            parse_uint,
            parse_version,
        )

        q = f.cmd
        try:
            if q == 1:
                bat = BatteryState.parse(f.payload, ear_type=self._guess_ear_type())
                self._emit("battery", bat.display(), bat)
                self._emit("log", f"电量：{bat.display()}")
            elif q == 2:
                self._emit("log", f"固件版本：{parse_version(f.payload)}")
            elif q == 3:
                name = parse_ascii(f.payload)
                if name:
                    first_time = not self.device_name
                    self.device_name = name
                    self._emit("log", f"设备名：{name}")
                    self._emit("state", f"已连接 · {name}")
                    # 把型号补进「已知设备」表：下次耳机不广播也能靠地址 + 名字直连
                    if self.address:
                        _save_known_device(self.address, name)
                    # 握手时 GET_BATTERY 与 GET_DEVICE_NAME 是并发发出的，电量很可能先到，
                    # 那时还不知道型号（单电池 vs 三值）。拿到名字后补读一次电量，
                    # 否则 Key Max 会一直显示成「左 -- / 右 xx% / 盒 --」。
                    # 注意 _interpret_query 是同步方法（在 bleak 回调线程里跑），
                    # 不能直接 await，用 create_task 投递到当前事件循环。
                    if first_time:
                        try:
                            asyncio.get_running_loop().create_task(
                                self._send_raw("GET_BATTERY（补读型号后）",
                                               self.protocol.request_battery()))
                        except RuntimeError:
                            pass  # 没有运行中的 loop（理论上不会发生），忽略即可
            elif q == 12:
                v = parse_uint(f.payload)
                try:
                    label = MafMode(v).label_cn
                except ValueError:
                    label = f"未知值 {v}"
                self._emit("log", f"降噪模式：{label}")
                # 单独的 maf 事件：GUI 用它回填「当前读回」，并据此判定写后校验成功/失败
                self._emit("maf", f"降噪模式：{label}", {"value": v, "label": label})
            elif q == 24:
                on = parse_bool(f.payload)
                self._emit("log", f"影院模式：{'开' if on else '关'}")
                self._emit("cinema", f"影院模式：{'开' if on else '关'}", {"on": on})
            elif q == 8:
                lv = parse_uint(f.payload)
                self._got_ll = True
                label = {0: "关闭", 1: "游戏", 2: "音乐", 3: "视频"}.get(lv, f"未知({lv})")
                self._emit("log", f"低延迟：{label}")
                # 独立事件：GUI 用它回填档位、并判定 SET 后读回是否一致。
                # Mate 机型只有 0/1，2/3 会被耳机拒绝（ACK payload=01），
                # 所以这里也可能收到 0/1 之外的历史值 —— 交给 GUI 决定是否提示机型不支持。
                self._emit("lowlatency", f"低延迟：{label}", {"value": lv, "label": label})
            elif q == 128:
                on = parse_bool(f.payload)
                self._got_mp = True
                self._emit("log", f"蓝牙畅连：{'开' if on else '关'}")
                self._emit("multipoint", f"蓝牙畅连：{'开' if on else '关'}", {"on": on})
            elif q == 130:
                tv = self.protocol.tone_volume(f)
                if tv is None:
                    self._emit("log", f"语音提示音量：无法解析（payload={f.payload.hex(' ')}）")
                else:
                    self._got_tone = True
                    self._emit("log", f"语音提示音量：{tv.label_cn}")
                    self._emit("tone", f"语音提示音量：{tv.label_cn}",
                               {"value": int(tv), "label": tv.label_cn})
            elif q == 142:
                unlocked = parse_bool(f.payload)
                self._got_ldac_unlock = True
                self._emit("log", f"LDAC 激活状态：{'已激活' if unlocked else '未激活'}")
                self._emit("ldac_unlock", f"LDAC 激活：{'已激活' if unlocked else '未激活'}",
                           {"unlocked": unlocked})
            elif q == 143:
                on = parse_bool(f.payload)
                self._got_ldac = True
                self._emit("log", f"LDAC 高清解码：{'开' if on else '关'}")
                self._emit("ldac", f"LDAC：{'开' if on else '关'}", {"on": on})
            elif q == 4:
                self._emit("log", f"EQ 曲线（{len(f.payload)}B）：{f.payload.hex(' ')}")
        except Exception as e:  # noqa: BLE001
            self._emit("log", f"（解析 cmd={q} 时出错：{e}）")

    # -- 静默监听（逆向用）---------------------------------------------------
    async def _start_listen(self, seconds: float) -> None:
        if self.client is None or not self.client.is_connected:
            self._emit("error", "未连接，无法监听")
            return
        if self._listen_task and not self._listen_task.done():
            self._emit("error", "已有监听任务在跑")
            return
        self._listening_since = time.time()
        self._listen_count = 0
        self._emit("log", f"[监听] 开始，{seconds:.0f} 秒内记录所有 notify/主动上报…")

        async def _wait() -> None:
            end = time.time() + seconds
            while time.time() < end:
                await asyncio.sleep(0.5)
            n = self._listen_count
            self._emit("log", f"[监听] {seconds:.0f} 秒结束，共 {n} 条")
            # 目标要求：60 秒静默监听未收到任何 Notify 时，提示用户先手动触发一次耳机操作
            if n == 0:
                self._emit("error",
                           "监听期间未收到任何 Notify。请先在耳机上做一次操作"
                           "（例如切换降噪 / 摘下耳机 / 按一下按键），再重新开始监听。")

        self._listen_task = asyncio.create_task(_wait())

    async def _stop_listen(self) -> None:
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            self._emit("log", "[监听] 已手动停止")


# ----------------------------------------------------------------------------
# 便捷入口：无 GUI 的脚本化调用（供回归测试 / 批量探测复用）
# ----------------------------------------------------------------------------


async def quick_probe(address: str, echo=print) -> dict[str, Any]:
    """一次性把设备读一遍，返回结构化结果。用于自检与 PROTOCOL_PC.md 复现。"""
    out: dict[str, Any] = {"address": address, "frames": []}
    proto = FiilProtocol()
    rx_frames: list[Frame] = []

    def on_notify(_c: Any, data: bytearray) -> None:
        for f in proto.feed(bytes(data)):
            rx_frames.append(f)

    async with BleakClient(address, timeout=20.0) as client:
        await client.start_notify(CHAR_NOTIFY_UUID, on_notify)
        await asyncio.sleep(0.3)

        async def send(name: str, frame: bytes, wait: float = 0.5) -> Frame | None:
            n0 = len(rx_frames)
            await client.write_gatt_char(CHAR_WRITE_UUID, frame, response=False)
            echo(f"[TX] {name:<20} {frame.hex(' ').upper()}")
            await asyncio.sleep(wait)
            new = rx_frames[n0:]
            for f in new:
                echo(f"    << {f.hex()}")
            out["frames"].append({"name": name, "tx": frame.hex(" "), "rx": [f.raw.hex(" ") for f in new]})
            return new[-1] if new else None

        await send("setUnlock", proto.request_unlock(), 0.6)
        for name, fr in (
            ("GET_VERSION", proto.request_version()),
            ("GET_BATTERY", proto.request_battery()),
            ("GET_DEVICE_NAME", proto.request_device_name()),
            ("GET_MAF", proto.request_maf()),
            ("GET_SPATIAL", proto.request_cinema()),
            ("GET_LOW_LATENCY", proto.request_low_latency()),
            ("GET_MULTIPOINT", proto.request_multipoint()),
            ("GET_TONE", proto.request_tone_volume()),
            ("GET_EQ", proto.request_eq()),
        ):
            await send(name, fr)

        await client.stop_notify(CHAR_NOTIFY_UUID)
    return out
