"""连接策略的离线测试：全假对象，不碰真实蓝牙。"""

from __future__ import annotations

import asyncio

from ble_connect import DeviceCache, connect_fast, supports_direct_address


class FakeClient:
    """按脚本决定第几次连接成功。"""

    def __init__(self, target, timeout, callback, *, fail: bool = False) -> None:
        self.target = target
        self.timeout = timeout
        self.callback = callback
        self.fail = fail
        self.connected = False
        self.disconnected = 0

    async def connect(self):
        if self.fail:
            raise RuntimeError("连不上")
        self.connected = True

    async def disconnect(self):
        self.connected = False
        self.disconnected += 1


class FakeScanner:
    def __init__(self, callback, device=None, delay=0.0, fail_start: bool = False) -> None:
        self.callback = callback
        self.device = device
        self.delay = delay
        self.fail_start = fail_start
        self.started = False
        self.stopped = 0

    async def start(self):
        if self.fail_start:
            # 后端本身坏了（蓝牙适配器关闭 / WinRT 未初始化）：扫描这一步没跑起来，
            # 此时「没找到设备」不能当作「设备不在广播」的证据。
            raise RuntimeError("扫描器不可用")
        self.started = True

    async def stop(self):
        self.stopped += 1


class FakeDevice:
    def __init__(self, address: str) -> None:
        self.address = address
        self.name = None
        self.details = None


def run(coro):
    return asyncio.run(coro)


def test_cache_hit_connects_without_scanning():
    cache = DeviceCache()
    device = FakeDevice("AA:BB:CC:DD:EE:FF")
    cache.remember(device)
    made: list = []
    scanned: list = []

    def factory(target, timeout, callback):
        made.append(target)
        return FakeClient(target, timeout, callback)

    def scanner_factory(cb):
        scanned.append(cb)
        return FakeScanner(cb)

    result = run(connect_fast(
        "aa:bb:cc:dd:ee:ff",
        cache=cache,
        client_factory=factory,
        scanner_factory=scanner_factory,
        platform="win32",
    ))
    assert result.ok and result.stage == "缓存直连"
    assert made == [device]
    assert scanned == []  # 完全没有扫描


def test_cached_device_failure_falls_through_and_forgets_it():
    cache = DeviceCache()
    cache.remember(FakeDevice("AA:BB:CC:DD:EE:FF"))
    calls: list = []

    def factory(target, timeout, callback):
        calls.append(target)
        # 缓存对象失败，第二次（合成地址）成功
        return FakeClient(target, timeout, callback, fail=len(calls) == 1)

    result = run(connect_fast(
        "AA:BB:CC:DD:EE:FF",
        cache=cache,
        client_factory=factory,
        scanner_factory=lambda cb: FakeScanner(cb),
        platform="win32",
    ))
    assert result.ok and result.stage == "地址直连"
    assert len(cache) == 0


def test_direct_address_used_before_scanning_on_windows():
    order: list[str] = []

    def factory(target, timeout, callback):
        order.append(getattr(target, "address", str(target)))
        return FakeClient(target, timeout, callback)

    result = run(connect_fast(
        "AA:BB:CC:DD:EE:FF",
        client_factory=factory,
        scanner_factory=lambda cb: FakeScanner(cb),
        platform="win32",
    ))
    assert result.ok and result.stage == "地址直连"
    assert order == ["AA:BB:CC:DD:EE:FF"]  # 只试了一次，没走到扫描


def test_non_windows_skips_synthetic_device():
    assert supports_direct_address("darwin") is False
    stages: list[str] = []
    device = FakeDevice("AA:BB:CC:DD:EE:FF")

    def scanner_factory(cb):
        scanner = FakeScanner(cb)

        async def emit():
            await asyncio.sleep(0)
            cb(device, None)

        asyncio.get_running_loop().create_task(emit())
        return scanner

    def factory(target, timeout, callback):
        stages.append(type(target).__name__)
        # 合成地址这条 Windows 专用路径不该出现；扫描到的对象也连不上，
        # 于是走到兜底那一步，用它确认兜底传的是纯字符串。
        return FakeClient(target, timeout, callback, fail=True)

    run(connect_fast(
        "AA:BB:CC:DD:EE:FF",
        client_factory=factory,
        scanner_factory=scanner_factory,
        scan_timeout=0.2,
        platform="darwin",
    ))
    assert stages[0] != "BLEDevice"  # 没构造合成地址对象
    assert "str" in stages  # 兜底用的是纯字符串


def test_scan_stops_as_soon_as_target_is_found():
    device = FakeDevice("AA:BB:CC:DD:EE:FF")
    scanners: list[FakeScanner] = []

    def scanner_factory(cb):
        scanner = FakeScanner(cb)
        scanners.append(scanner)

        async def emit():
            await asyncio.sleep(0)
            cb(device, None)

        asyncio.get_running_loop().create_task(emit())
        return scanner

    result = run(connect_fast(
        "AA:BB:CC:DD:EE:FF",
        client_factory=lambda t, to, cb: FakeClient(t, to, cb),
        scanner_factory=scanner_factory,
        platform="darwin",
    ))
    assert result.ok and result.stage == "扫描后连接"
    assert scanners[0].stopped == 1


def test_scan_ignores_other_addresses():
    device = FakeDevice("11:22:33:44:55:66")

    def scanner_factory(cb):
        scanner = FakeScanner(cb)

        async def emit():
            await asyncio.sleep(0)
            cb(device, None)

        asyncio.get_running_loop().create_task(emit())
        return scanner

    result = run(connect_fast(
        "AA:BB:CC:DD:EE:FF",
        client_factory=lambda t, to, cb: FakeClient(t, to, cb, fail=True),
        scanner_factory=scanner_factory,
        scan_timeout=0.2,
        platform="darwin",
    ))
    # 只看到别的设备 → 目标不在广播，直接带原因失败，不再兜底空转
    assert result.ok is False
    assert result.reason == "not_advertising"
    assert [a.stage for a in result.attempts] == ["扫描寻找"]


def test_all_paths_fail_reports_error_and_timings():
    result = run(connect_fast(
        "AA:BB:CC:DD:EE:FF",
        client_factory=lambda t, to, cb: FakeClient(t, to, cb, fail=True),
        scanner_factory=lambda cb: FakeScanner(cb),
        scan_timeout=0.1,
        platform="win32",
    ))
    assert result.ok is False
    assert result.error
    # 扫不到目标就不再兜底：省掉的是最后那 20 秒白等
    assert [a.stage for a in result.attempts] == ["地址直连", "扫描寻找"]
    assert "合计" in result.summary()


def test_cache_ttl_expires():
    now = [100.0]
    cache = DeviceCache(ttl=10.0, clock=lambda: now[0])
    device = FakeDevice("AA:BB:CC:DD:EE:FF")
    cache.remember(device)
    assert cache.get("AA:BB:CC:DD:EE:FF") is device
    now[0] += 11
    assert cache.get("AA:BB:CC:DD:EE:FF") is None
    assert len(cache) == 0


def test_stage_callback_reports_progress():
    seen: list[str] = []
    run(connect_fast(
        "AA:BB:CC:DD:EE:FF",
        client_factory=lambda t, to, cb: FakeClient(t, to, cb, fail=True),
        scanner_factory=lambda cb: FakeScanner(cb),
        scan_timeout=0.1,
        platform="win32",
        on_stage=seen.append,
    ))
    assert seen == ["地址直连", "扫描寻找"]


# ---------------------------------------------------------------- 失败归因
# 用户实际遇到的日志：地址直连 0.12s → 扫描寻找 8.04s → 地址兜底 20.00s，合计 28 秒
# 才弹「连接失败」。慢的不是那 0.12 秒，而是后面两段纯粹的等待：
#   * 扫了 8 秒没扫到 —— 耳机被经典蓝牙占用时不广播，这 8 秒注定白花；
#   * 兜底又交给 Bleak 自己扫 20 秒 —— 前面已经在扫了，这里等于重复劳动。
# 真正有用的信息是「扫描从头到尾没看见这个地址」：它说明耳机不在广播，
# 应该立刻给出可操作的提示，而不是再等 20 秒。


def test_scan_miss_skips_slow_fallback_and_explains_why():
    """扫不到目标时不要再去兜底空转 20 秒，直接带原因失败。"""
    result = run(connect_fast(
        "AA:BB:CC:DD:EE:FF",
        client_factory=lambda t, to, cb: FakeClient(t, to, cb, fail=True),
        scanner_factory=lambda cb: FakeScanner(cb),
        scan_timeout=0.1,
        connect_timeout=20.0,
        platform="win32",
    ))
    assert result.ok is False
    # 兜底不再跑：验证记录里没有「地址兜底」
    assert [a.stage for a in result.attempts] == ["地址直连", "扫描寻找"]
    # 失败原因要指向真正的问题（不在广播），而不是一句泛泛的 TimeoutError
    assert result.reason == "not_advertising"
    assert "广播" in result.error


def test_scan_failure_in_backend_does_not_claim_device_is_off():
    """扫描器自己起不来（适配器关闭）时不能说「耳机没在广播」——那是两码事。"""
    result = run(connect_fast(
        "AA:BB:CC:DD:EE:FF",
        client_factory=lambda t, to, cb: FakeClient(t, to, cb, fail=True),
        scanner_factory=lambda cb: FakeScanner(cb, fail_start=True),
        scan_timeout=0.1,
        connect_timeout=20.0,
        platform="win32",
    ))
    assert result.ok is False
    assert result.reason == "scan_failed"
    # 扫描后端坏了要重试兜底那条路，因为它可能是唯一还能用的路径
    assert [a.stage for a in result.attempts] == ["地址直连", "扫描寻找", "地址兜底"]


def test_successful_scan_still_uses_address_fallback_when_connect_fails():
    """扫到了但连不上：这时兜底有意义（可能是缓存对象陈旧），要保留。"""
    device = FakeDevice("AA:BB:CC:DD:EE:FF")

    def scanner_factory(cb):
        scanner = FakeScanner(cb)

        async def emit():
            await asyncio.sleep(0)
            cb(device, None)

        asyncio.get_running_loop().create_task(emit())
        return scanner

    calls: list = []

    def factory(target, timeout, callback):
        calls.append(target)
        # 前两次（合成地址 + 扫描到的对象）都失败，兜底那次成功
        return FakeClient(target, timeout, callback, fail=len(calls) <= 2)

    result = run(connect_fast(
        "AA:BB:CC:DD:EE:FF",
        client_factory=factory,
        scanner_factory=scanner_factory,
        scan_timeout=0.2,
        platform="win32",
    ))
    assert result.ok and result.stage == "地址兜底"
    assert [a.stage for a in result.attempts] == ["地址直连", "扫描寻找", "扫描后连接", "地址兜底"]
