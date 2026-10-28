"""蓝牙连接策略：能直连就不要先白扫 20 秒。

## 为什么需要这个模块（实测依据）

* Windows 上 `BleakClient("AA:BB:CC:..")` 收到字符串地址时会先跑
  `BleakScanner.find_device_by_address(timeout=...)`，只有扫到目标才继续建连。
  耳机此刻不广播时，这个扫描会一直空转到超时 —— 默认 20 秒，用户看到的就是
  「点连接后长时间没反应，然后报找不到设备」。
* FIIL 双模耳机被经典蓝牙占用时会**停止 BLE 广播**，所以「扫不到」是常态而不是故障。
* 一旦手里有扫描得到的 `BLEDevice`，`BleakClient(device)` 会跳过这一轮扫描，
  直接走 `BluetoothLEDevice.from_bluetooth_address_async`（实测 0.1~0.3 秒返回）。

## 策略（代价从低到高，成功即停）

1. **缓存直连** —— 上次扫描记住的 `BLEDevice`（TTL 内）直接建连，短超时。
2. **地址直连** —— 合成 `BLEDevice(address, None, None)`，同样跳过扫描；
   设备在 Windows 缓存里时秒连，不在时也只会花 0.1 秒失败，不是 20 秒。
3. **边扫边连** —— 扫描过程中一发现目标地址就立刻停扫并连接，不等满 scan_timeout。
4. **地址兜底** —— 纯字符串交给 Bleak 自己扫（最慢，但兼容性最好）。

每一步的耗时都记在 `ConnectResult.attempts` 里，界面直接显示「本次连接走哪条路径、
各花多久」，用户能看出到底慢在哪一段。
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: 缓存 BLEDevice 的有效期（秒）。耳机重新广播后地址一般不变，但对象可能失效，
#: 所以给一个不长的 TTL：过期就重新扫，避免一直拿陈旧对象去连。
CACHE_TTL = 300.0


@dataclass(frozen=True)
class ConnectAttempt:
    """一次连接尝试的耗时记录。"""

    stage: str
    seconds: float
    ok: bool
    detail: str = ""


@dataclass
class ConnectResult:
    """连接结果。`client` 为 None 表示全部路径都失败，`error` 说明最后一步的原因。

    `reason` 是给上层做判断用的机器可读失败归因（空串表示成功）：

    * `not_advertising` —— 完整扫了一轮，目标地址始终没出现。双模耳机被经典
      蓝牙占用时会停止 BLE 广播，这是最常见的一种，提示要写成「耳机不在广播」
      而不是「超时」。
    * `scan_failed` —— 扫描器本身起不来（适配器关闭 / WinRT 未就绪）。此时
      「没扫到」不能当作设备不在广播的证据，还得走兜底那条路。
    * `connect_failed` —— 扫到了（或直接按地址试过），但每次建连都失败。
    """

    client: Any | None = None
    stage: str = ""
    attempts: list[ConnectAttempt] = field(default_factory=list)
    error: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.client is not None

    @property
    def total_seconds(self) -> float:
        return sum(a.seconds for a in self.attempts)

    def summary(self) -> str:
        """一行可读的耗时摘要，直接写进日志。"""
        if not self.attempts:
            return "未尝试连接"
        parts = " → ".join(f"{a.stage} {a.seconds:.2f}s" for a in self.attempts)
        return f"{parts}（合计 {self.total_seconds:.2f}s）"


class DeviceCache:
    """记住扫到的 BLEDevice，供下次直连复用。"""

    def __init__(self, ttl: float = CACHE_TTL, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._items: dict[str, tuple[Any, float]] = {}

    @staticmethod
    def _key(address: str) -> str:
        return address.strip().upper()

    def remember(self, device: Any) -> None:
        address = getattr(device, "address", "")
        if not address:
            return
        self._items[self._key(address)] = (device, self._clock())

    def get(self, address: str) -> Any | None:
        item = self._items.get(self._key(address))
        if item is None:
            return None
        device, stamp = item
        if self._clock() - stamp > self._ttl:
            self._items.pop(self._key(address), None)
            return None
        return device

    def forget(self, address: str) -> None:
        self._items.pop(self._key(address), None)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


#: (目标, 超时, 断线回调) -> BleakClient 之类；测试里替换成假对象。
ClientFactory = Callable[[Any, float, Any], Any]
#: (detection_callback) -> 有 start/stop 的扫描器。
ScannerFactory = Callable[[Callable[[Any, Any], None]], Any]


def _default_client_factory(target: Any, timeout: float, callback: Any) -> Any:
    from bleak import BleakClient  # noqa: PLC0415 - 延迟导入，便于离线测试

    return BleakClient(target, timeout=timeout, disconnected_callback=callback)


def _default_scanner_factory(detection_callback: Callable[[Any, Any], None]) -> Any:
    from bleak import BleakScanner  # noqa: PLC0415

    return BleakScanner(detection_callback=detection_callback)


def synthetic_device(address: str) -> Any:
    """构造只有地址的 BLEDevice：WinRT 后端据此跳过扫描，直接按地址建连。"""
    from bleak.backends.device import BLEDevice  # noqa: PLC0415

    return BLEDevice(address, None, None)


def supports_direct_address(platform: str | None = None) -> bool:
    """只有 Windows(WinRT) 后端能用「只有地址的 BLEDevice」跳过扫描。

    macOS 的 BLEDevice.details 必须是 CBPeripheral 的 UUID，构造不出来；
    Linux(BlueZ) 也要求 details 是 D-Bus 路径。
    """
    return (platform or sys.platform) == "win32"


async def _try_connect(
    target: Any,
    *,
    timeout: float,
    client_factory: ClientFactory,
    disconnected_callback: Any,
    stage: str,
) -> tuple[Any | None, ConnectAttempt]:
    started = time.perf_counter()
    client = None
    try:
        client = client_factory(target, timeout, disconnected_callback)
        await asyncio.wait_for(client.connect(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - 任何失败都只是「这条路不通」
        elapsed = time.perf_counter() - started
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        return None, ConnectAttempt(stage, elapsed, False, f"{type(exc).__name__}: {exc}")
    return client, ConnectAttempt(stage, time.perf_counter() - started, True)


async def _scan_until_found(
    address: str,
    *,
    timeout: float,
    scanner_factory: ScannerFactory,
) -> tuple[Any | None, float, str, bool]:
    """扫到目标地址就立刻返回，不等满 timeout。

    返回 `(device, 耗时, 说明, 扫描是否正常跑完)`。最后那个布尔值用来区分
    「扫完了但目标不在广播」和「扫描器根本没起来」——这两种情况的后续动作
    完全不同：前者可以直接收工报原因，后者还得试兜底路径。
    """
    started = time.perf_counter()
    loop = asyncio.get_running_loop()
    found: asyncio.Future = loop.create_future()

    def on_detect(device: Any, _adv: Any) -> None:
        if found.done():
            return
        if str(getattr(device, "address", "")).strip().upper() == address.strip().upper():
            found.set_result(device)

    scanner = scanner_factory(on_detect)
    try:
        await scanner.start()
    except Exception as exc:  # noqa: BLE001
        return None, time.perf_counter() - started, f"{type(exc).__name__}: {exc}", False
    try:
        device = await asyncio.wait_for(found, timeout=timeout)
    except TimeoutError:
        return None, time.perf_counter() - started, f"{timeout:.0f} 秒内未扫到该地址", True
    except Exception as exc:  # noqa: BLE001
        return None, time.perf_counter() - started, f"{type(exc).__name__}: {exc}", False
    finally:
        try:
            await scanner.stop()
        except Exception:  # noqa: BLE001
            pass
    return device, time.perf_counter() - started, "", True


def _advertising_hint(address: str) -> str:
    """扫不到目标时给用户看的原因：说清楚是什么状况，并给出能做的动作。"""
    return (
        f"未发现 {address} 的 BLE 广播。FIIL 双模耳机被经典蓝牙占用（正在放音频）时"
        "会停止广播，此时按地址直连也会失败。请先把耳机从 Windows 音频设备里断开"
        "（或关掉正在播放的声音），等 BLE 重新广播后再点连接。"
    )


async def connect_fast(
    address: str,
    *,
    cache: DeviceCache | None = None,
    scan_timeout: float = 8.0,
    direct_timeout: float = 6.0,
    connect_timeout: float = 20.0,
    disconnected_callback: Any = None,
    client_factory: ClientFactory | None = None,
    scanner_factory: ScannerFactory | None = None,
    platform: str | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> ConnectResult:
    """按「代价从低到高」的顺序尝试连接，返回第一个成功的 client。"""
    client_factory = client_factory or _default_client_factory
    scanner_factory = scanner_factory or _default_scanner_factory
    address = address.strip().upper()
    result = ConnectResult()

    def note(stage: str) -> None:
        if on_stage is not None:
            on_stage(stage)

    # 1) 缓存里的 BLEDevice：直接建连，跳过扫描
    if cache is not None:
        cached = cache.get(address)
        if cached is not None:
            note("缓存直连")
            client, attempt = await _try_connect(
                cached,
                timeout=min(direct_timeout, connect_timeout),
                client_factory=client_factory,
                disconnected_callback=disconnected_callback,
                stage="缓存直连",
            )
            result.attempts.append(attempt)
            if client is not None:
                result.client, result.stage = client, "缓存直连"
                return result
            cache.forget(address)  # 缓存对象失效，别再拿它试

    # 2) 合成地址对象：同样跳过扫描，失败也只花零点几秒
    if supports_direct_address(platform):
        note("地址直连")
        client, attempt = await _try_connect(
            synthetic_device(address),
            timeout=min(direct_timeout, connect_timeout),
            client_factory=client_factory,
            disconnected_callback=disconnected_callback,
            stage="地址直连",
        )
        result.attempts.append(attempt)
        if client is not None:
            result.client, result.stage = client, "地址直连"
            return result

    # 3) 边扫边连：一发现就停扫
    note("扫描寻找")
    device, seconds, detail, scan_ran = await _scan_until_found(
        address, timeout=scan_timeout, scanner_factory=scanner_factory
    )
    if device is not None:
        if cache is not None:
            cache.remember(device)
        client, attempt = await _try_connect(
            device,
            timeout=connect_timeout,
            client_factory=client_factory,
            disconnected_callback=disconnected_callback,
            stage="扫描后连接",
        )
        result.attempts.append(ConnectAttempt("扫描寻找", seconds, True, ""))
        result.attempts.append(attempt)
        if client is not None:
            result.client, result.stage = client, "扫描后连接"
            return result
        result.error = attempt.detail
    else:
        result.attempts.append(ConnectAttempt("扫描寻找", seconds, False, detail))
        result.error = detail
        if scan_ran:
            # 扫描跑完了一整轮，目标地址依然没出现 —— 这就已经是最终答案了。
            # 再交给 Bleak 兜底只会把同一轮扫描重跑一遍（实测又是 20 秒），
            # 用户看到的「28 秒后才报错」就是这么来的。
            result.reason = "not_advertising"
            result.error = _advertising_hint(address)
            return result

    # 4) 地址兜底：交给 Bleak 自己处理（其他平台或不支持合成对象的后端，
    #    以及扫到了却连不上、或扫描器根本没起来的情况）
    note("地址兜底")
    client, attempt = await _try_connect(
        address,
        timeout=connect_timeout,
        client_factory=client_factory,
        disconnected_callback=disconnected_callback,
        stage="地址兜底",
    )
    result.attempts.append(attempt)
    if client is not None:
        result.client, result.stage = client, "地址兜底"
        return result
    result.error = attempt.detail or result.error
    result.reason = result.reason or ("connect_failed" if device is not None else "scan_failed")
    return result
