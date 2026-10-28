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
    def __init__(self, callback, device=None, delay=0.0) -> None:
        self.callback = callback
        self.device = device
        self.delay = delay
        self.started = False
        self.stopped = 0

    async def start(self):
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

    def factory(target, timeout, callback):
        stages.append(type(target).__name__)
        return FakeClient(target, timeout, callback)

    result = run(connect_fast(
        "AA:BB:CC:DD:EE:FF",
        client_factory=factory,
        scanner_factory=lambda cb: FakeScanner(cb),
        scan_timeout=0.1,  # 别真等满 8 秒，测试要快
        platform="darwin",
    ))
    assert result.ok
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
        client_factory=lambda t, to, cb: FakeClient(t, to, cb),
        scanner_factory=scanner_factory,
        scan_timeout=0.2,
        platform="darwin",
    ))
    # 扫描没找到目标 → 走地址兜底并成功
    assert result.ok and result.stage == "地址兜底"


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
    assert [a.stage for a in result.attempts] == ["地址直连", "扫描寻找", "地址兜底"]
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
    assert seen == ["地址直连", "扫描寻找", "地址兜底"]
