r"""FIIL 耳机 BLE-GATT 协议封装（供 PC 探针与 Android 模块复用）

本文件里每一条指令都标注了：
    - 特征值 UUID
    - 发送字节（Hex，含 seq 占位）
    - 预期回包
    - 验证状态：CONFIRMED（本机实测有回包且读回一致）/ PARTIAL（有回包但读回不一致或语义未定）/ UNVERIFIED（无实测证据）

禁止臆造：凡未实测的字节一律不写进语义化方法，只保留在「原始 Hex 控制台」。

证据来源：
    docs/evidence/table_20261025_002133.txt   （13 条 GET 全表）
    docs/evidence/set4_20261025_003607.txt    （SET 全表 + 读回）
    逆向：com.fengeek.main.i.b.writeMate / f.java / c.java
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

# ----------------------------------------------------------------------------
# GATT 常量（实测枚举得到，见 docs/evidence/gatt_20261025_001610.json）
# ----------------------------------------------------------------------------

SERVICE_UUID = "0000fdb3-0000-1000-8000-00805f9b34fb"
"""Vendor specific 主服务。实测该设备只有这一个服务。"""

CHAR_NOTIFY_UUID = "0000ff18-0000-1000-8000-00805f9b34fb"
"""设备→主机：notify + read，handle=2。CCCD 00002902 handle=4。"""

CHAR_WRITE_UUID = "0000ff17-0000-1000-8000-00805f9b34fb"
"""主机→设备：write-without-response + write，handle=7。**命令都发这里**。"""

CHAR_ALT_UUID = "0000ff16-0000-1000-8000-00805f9b34fb"
"""write + read，handle=5。实测写入返回 False（不支持），read 返回空。
   ⚠️ 不要往 ff16 发命令——网上很多 FIIL 资料写的是 ff16，是错的。"""

CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"


# ----------------------------------------------------------------------------
# 帧结构
# ----------------------------------------------------------------------------
#
#  偏移  字段        说明
#   0    seq        低 4 位滚动计数（0..15），高 4 位恒 0
#   1    cid        classifyId：GET 恒为 39；SET 为命令族编号
#   2    type       0x01=REQUEST 0x02=RESPONSE 0x03=NOTIFY
#   3    reserved   恒 0x00
#   4    lenField   cmdId != 0 时为 payloadLen + 2
#   5    cmdId      仅 cmdId != 0 时存在
#   6    payloadLen 仅 cmdId != 0 时存在（cmdId==0 时该位置就是 payloadLen）
#   ...  payload
#
# GET 例：00 27 01 00 02 0C 00            查 MAF（cid=39, cmd=12, 无 payload）
# SET 例：02 2C 01 00 01 02               设 MAF=2（cid=44, cmd=0, payload={2}）

TYPE_REQUEST = 0x01
TYPE_RESPONSE = 0x02
TYPE_NOTIFY = 0x03

CID_QUERY = 39
"""GET 类命令的固定 classifyId。"""


class Cid(IntEnum):
    """SET 类命令的命令族编号（即 classifyId）。来源：com.fengeek.main.i.f.java"""

    SET_PARAM = 1        # f.java setChangLan/setTone/setEQAdaptive/...
    SET_EQ = 32          # f.java setEQ
    SET_MUSIC = 33       # f.java setMusicStatus/setNextMusic/setPreviousMusic/setAddVolume
    SET_RESET = 36       # f.java setReset
    SET_LATENCY = 37     # f.java setLowDelay
    SET_WEAR = 38        # 佩戴检测开关（F051/F053）
    SET_UNLOCK = 39      # f.java setUnlock（cmdId=-1）
    SET_LANGUAGE = 41    # f.java setLanguage
    SET_MAF = 44         # f.java setMAF
    SET_SPATIAL = 50     # c.java setSpatialAudio


class Query(IntEnum):
    """GET 类命令的 commandId（cid 恒为 39）。来源：com/fengeek/main/i/d/c.java

    全部 16 条已于 2026-10-25T01:44 实机验证（docs/evidence/unverified_cmdid_20261025_014438.txt）。
    注意：jadx 把负数 cmdId 解析成了 com.baidu.turbonet.net.NetError 常量名，
    下表已按 NetError.java 的实际值还原（如 ERR_NO_SSL_VERSIONS_ENABLED = -112 = 0x90）。
    """

    BATTERY = 1          # getElectricity     c.java:75
    VERSION = 2          # getVersion         c.java:267
    DEVICE_NAME = 3      # 实测返回 ASCII "FIIL Key Max"，非 MAC
    EQ = 4               # getEQStatus        c.java:59
    KEY = 5              # getKey             c.java:91  ← 触摸按键配置表（10 组 3 字节）
    MUSIC_STATUS = 7     # getMusicStatus     c.java:187
    LOW_LATENCY = 8      # getLowDelayStatus  c.java:155
    WEAR = 9             # 实测无响应（本机型不支持）
    MAF = 12             # getMAFStatus       c.java:171
    SPATIAL = 24         # getSpatialAudioStatus c.java:235
    CHANGLAN = 128       # getChangLanStatus  c.java:27   畅连（多点）
    TONE = 130           # getToneStatus      c.java:251  NetError -126
    PID = 132            # getPID             c.java:203
    LEFT_UUID = 138      # getLeftHeadsetUUID c.java:139  NetError -118
    RIGHT_UUID = 139     # getRightHeadsetUUID c.java:219 NetError -117
    LDAC_UNLOCK = 142    # getLdacUnlockStatus c.java:123 NetError -114
    LDAC_SWITCH = 143    # getLdacSwitchStatus c.java:107 NetError -113
    CODEC = 144          # getCodecStatus     c.java:43   NetError -112
    UNLOCK = 255         # setUnlock（cid=39, cmdId=-1&0xFF）


class MafMode(IntEnum):
    """降噪（MAF）模式。文案取自 FIIL+ 布局 fragment_f46/f49/f51/f53_style.xml 原文。"""

    OFF = 0
    NOISE_CANCEL = 1
    OPEN = 2      # FIIL 原文「开放」
    LISTEN = 3    # FIIL 原文「兼听」
    WIND = 4      # FIIL 原文「风中」

    @property
    def label_cn(self) -> str:
        return {
            MafMode.OFF: "关闭",
            MafMode.NOISE_CANCEL: "降噪",
            MafMode.OPEN: "开放",
            MafMode.LISTEN: "兼听",
            MafMode.WIND: "风中",
        }[self]


class ToneVolume(IntEnum):
    """语音提示音量（内置提示音 + 语音播报的音量）。

    文案与顺序取自 FIIL+ `res/values/arrays.xml:209-214` 的 `f43_select_operate_volume`：
    音量高 / 音量中(默认) / 音量低 / 静音。

    ⚠️ 注意线值与文案是**反序**的：0=高音量、3=静音。
    这一点在 `com/fengeek/main/f046/fragment/F046HomeFragment.java:524-538` `setVolume(int)` 里
    逐档写死（`volume==3 → "静音"`、`==2 → "提示音-低"`、`==1 → "提示音-中"`、`==0 → "提示音-高"`），
    不是常规的「越大越响」，别按直觉实现。
    """

    HIGH = 0      # 音量高（默认）
    MID = 1       # 音量中
    LOW = 2       # 音量低
    MUTE = 3      # 静音

    @property
    def label_cn(self) -> str:
        return {
            ToneVolume.HIGH: "音量高",
            ToneVolume.MID: "音量中",
            ToneVolume.LOW: "音量低",
            ToneVolume.MUTE: "静音",
        }[self]


class LowLatencyMode(IntEnum):
    """低延迟档位。

    ⚠️ 机型差异（本轮实测确认，见 docs/evidence/ll4_20261025.txt）：
      * GAIA 老机型：四档 0=关闭 / 1=游戏 / 2=音乐 / 3=视频
      * **Mate 机型（含本机 Key Max）：只有两档 0=关闭 / 1=开启**。
        SET 2 和 SET 3 时耳机在 ACK 里明确返回 payload=01（失败），读回仍是 1 ——
        不是「延迟没生效」，是耳机直接拒绝了该档位。
    """

    OFF = 0
    ON = 1        # Mate：唯一的「开启」；GAIA：游戏
    MUSIC = 2     # 仅 GAIA
    VIDEO = 3     # 仅 GAIA

    @property
    def label_cn(self) -> str:
        return {
            LowLatencyMode.OFF: "关闭",
            LowLatencyMode.ON: "开启（游戏）",
            LowLatencyMode.MUSIC: "音乐",
            LowLatencyMode.VIDEO: "视频",
        }[self]


class FrameType(IntEnum):
    REQUEST = TYPE_REQUEST
    RESPONSE = TYPE_RESPONSE
    NOTIFY = TYPE_NOTIFY


# ----------------------------------------------------------------------------
# 帧编解码
# ----------------------------------------------------------------------------


def build_frame(cid: int, cmd: int, payload: bytes = b"", seq: int = 0) -> bytes:
    """按 writeMate 的规则组帧（与 com.fengeek.main.i.b.writeMate 逐字节一致）。"""
    s = seq & 0x0F
    plen = len(payload)
    head = [s, cid & 0xFF, TYPE_REQUEST, 0x00]
    if cmd != 0:
        head.append((plen + 2) & 0xFF)
        head.append(cmd & 0xFF)
    head.append(plen & 0xFF)
    return bytes(head) + payload


@dataclass
class Frame:
    raw: bytes
    seq: int
    cid: int
    ftype: int
    cmd: int | None
    payload: bytes

    @property
    def is_response(self) -> bool:
        return self.ftype == TYPE_RESPONSE

    @property
    def is_notify(self) -> bool:
        return self.ftype == TYPE_NOTIFY

    @property
    def ok(self) -> bool | None:
        """SET 类回包：payload[0]==0 表示成功，1 表示失败。GET 类不适用。"""
        if not self.is_response or not self.payload:
            return None
        return self.payload[0] == 0

    def hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.raw)

    def describe(self) -> str:
        t = {1: "REQ", 2: "RESP", 3: "NOTIFY"}.get(self.ftype, f"0x{self.ftype:02X}")
        return f"{t} cid={self.cid} cmd={self.cmd} len={len(self.payload)}"


def parse_frame(buf: bytes) -> Frame | None:
    """解析单帧；数据不足或不是合法帧时返回 None。

    完整帧布局（lenField >= 2 时）::

        [0]seq [1]cid [2]type [3]reserved [4]lenField [5]cmdId [6]payloadLen [7..]payload
                                          └── = plen + 2 ──┘

    总长 = 5 + lenField。例：`0A 27 02 00 05 01 03 00 50 00`
        lenField=0x05 → 总长 10；plen=3；cmd=0x01；payload=`00 50 00` ✓
    """
    if len(buf) < 5:
        return None
    if buf[2] not in (TYPE_REQUEST, TYPE_RESPONSE, TYPE_NOTIFY):
        return None
    if buf[3] != 0x00:
        return None
    seq, cid, ftype = buf[0], buf[1], buf[2]
    len_field = buf[4]
    if len_field == 0:
        # 真·空帧：耳机回「这条命令我不认识」时给 5 字节无负载帧
        return Frame(raw=buf[:5], seq=seq, cid=cid, ftype=ftype, cmd=None, payload=b"")
    if len_field == 1:
        # 无 cmdId 的短帧：cmdId 被省略，长度字段本身就是 payloadLen。
        # 典型是 **SET 命令的应答**：`xx cc 02 00 01 00` → payload=[0x00] 成功
        total = 6
        if len(buf) < total:
            return None
        return Frame(raw=buf[:total], seq=seq, cid=cid, ftype=ftype, cmd=None,
                     payload=buf[5:6])
    total = 5 + len_field
    if len(buf) < total:
        return None
    cmd = buf[5]
    plen = len_field - 2
    payload = buf[7:7 + plen]
    return Frame(raw=buf[:total], seq=seq, cid=cid, ftype=ftype, cmd=cmd, payload=payload)


def drain(buf: bytes) -> tuple[list[Frame], bytes]:
    """从缓冲区里连续解出所有完整帧，返回 (帧列表, 剩余未用字节)。

    ⚠️ 必须区分「数据不足」与「帧头非法」：
      * 数据不足 → 保留从当前位置起的全部字节，等下次 notify 再拼
      * 帧头非法 → 丢 1 字节重同步
    早期实现一律 `pos += 1`，会把半帧直接吞掉 —— 这是这次实测抓到的真实缺陷。
    """
    frames: list[Frame] = []
    pos = 0
    n = len(buf)
    while pos < n:
        avail = n - pos
        if avail < 5:
            break  # 连帧头都不够，保留
        head = buf[pos:pos + 5]
        if head[2] not in (TYPE_REQUEST, TYPE_RESPONSE, TYPE_NOTIFY) or head[3] != 0x00:
            pos += 1  # 帧头非法，重同步
            continue
        len_field = head[4]
        if len_field == 0:
            need = 5          # 真·空帧
        elif len_field == 1:
            need = 6          # 无 cmdId 短帧（SET 应答），首字节即 payload[0]
        else:
            need = 5 + len_field
        if avail < need:
            break  # 半帧，保留等待后续字节
        f = parse_frame(buf[pos:pos + need])
        if f is None:
            pos += 1
            continue
        frames.append(f)
        pos += len(f.raw)
    return frames, buf[pos:]


# ----------------------------------------------------------------------------
# 响应解析
# ----------------------------------------------------------------------------


@dataclass
class BatteryState:
    """电量。

    实测回包：`0A 27 02 00 05 01 03 00 50 00`
              帧头 5B   cmd=1  len=3  payload = 00 50 00
              本机 Key Max 稳定回 `00 4D 00`（0x4D=77%），20 次采样 20 次一致
              （`exp_battery.py` → `docs/evidence/battery_20261025.txt`）。

    三字节语义（FIIL 官方源码逐机型核对）：

    | 机型族 | 实现 | 字节含义 |
    |---|---|---|
    | F045 / F047 / F049 / F051 / F053 | `o(buff[0]); r(buff[1]); m(buff[2])` | 左耳 / 右耳 / 充电盒 |
    | **F046（Key Max）** | `m(buff[1])` | **只有单电池，取 payload[1]** |
    | F042 | `buff[0] != 0` 判存在，再用 `buff[1]` | 单电池 |

    依据：
    - `jadx_out/sources/com/fengeek/main/f046/fragment/F046HomeFragment.java:623`
      `public void setElectricity(byte[] buff) { m(buff[1]); }`
    - `...\\f045\\fragment\\F045HomeFragment.java:546`
      `setElectricity` → `o(buff[0])`（左）/ `r(buff[1])`（右）/ `m(buff[2])`（盒）
    - `...\\f042\\fragment\\F042HomeFragment.java:440`
      `if (buff.length < 2 || buff[0] != 0)` 判有效后 `D(buff[1])`

    **因此本机 Key Max 只有「一个电池」，不应显示成「右耳」。**
    `parse(..., ear_type=)` 传入 FIIL 型号号即可自动选对呈现方式；
    ear_type 未知时按三值解析（回包里另外两字节本来就是 0，会显示 `--`）。

    0 与 255 都视为「无数据」，显示为 --。
    """

    left: int | None = None
    right: int | None = None
    case: int | None = None
    # 单电池机型（头戴/单耳）把唯一电量放这里，避免误标成「右耳」
    single: int | None = None

    #: FIIL earType 中属于「单电池」的型号
    #: 只有 F042 / F046 两族源码里是单字节（`buff[1]`）；
    #: F045/F047/F048/F049/F051/F053 都是 `buff[0]=左 / buff[1]=右 / buff[2]=盒` 三值，
    #: 不要因为「头戴式」的直觉把它们误归到单电池。
    SINGLE_CELL_EAR_TYPES = frozenset({42, 46})

    @staticmethod
    def _norm(v: int) -> int | None:
        return None if v <= 0 or v == 255 else v

    @classmethod
    def parse(cls, payload: bytes, ear_type: int | None = None) -> "BatteryState":
        # 单电池机型：官方只读 payload[1]
        if ear_type in cls.SINGLE_CELL_EAR_TYPES and len(payload) >= 2:
            return cls(single=cls._norm(payload[1]))
        if len(payload) >= 3:
            return cls(
                left=cls._norm(payload[0]),
                right=cls._norm(payload[1]),
                case=cls._norm(payload[2]),
            )
        if len(payload) == 2:
            return cls(left=cls._norm(payload[0]), right=cls._norm(payload[1]))
        if len(payload) == 1:
            return cls(right=cls._norm(payload[0]))
        return cls()

    def display(self) -> str:
        def f(v: int | None) -> str:
            return "--" if v is None else f"{v}%"

        if self.single is not None or (
            self.left is None and self.right is None and self.case is None
        ):
            return f"耳机 {f(self.single)}"
        return f"左 {f(self.left)} / 右 {f(self.right)} / 盒 {f(self.case)}"


def parse_version(payload: bytes) -> str | None:
    """版本回包 payload = `03 00 01 01` → 字符串 "3.0.1.1"（**顺序直读，不反转**）。

    实测：`01 27 02 00 06 02 04 03 00 01 01`
          → cmd=02 lenField=04 → payload=`03 00 01 01` → 固件 3.0.1.1
    """
    if len(payload) < 4:
        return None
    return ".".join(str(payload[i]) for i in range(4))


def parse_ascii(payload: bytes) -> str:
    """设备名回包 payload = ASCII。"""
    return payload.rstrip(b"\x00").decode("ascii", errors="replace")


def parse_uint(payload: bytes) -> int | None:
    return payload[0] if payload else None


def parse_bool(payload: bytes) -> bool | None:
    if not payload:
        return None
    return payload[0] == 1


# ----------------------------------------------------------------------------
# FiilProtocol：语义化控制层
# ----------------------------------------------------------------------------


class FiilProtocol:
    """FIIL Mate 协议语义封装。

    使用方式（由 BleWorker 提供 writer / reader 抽象）：

        proto = FiilProtocol()
        proto.set_maf(MafMode.NOISE_CANCEL)   # 返回要发送的字节
        ...
        proto.feed(rx_bytes)                  # 把 notify 收到的字节喂进来
        proto.take_frames()                   # 取出解析好的帧

    这样 PC 探针与 Android 模块可以共用同一份组帧/解析代码，只有传输层不同。
    """

    def __init__(self) -> None:
        self._seq = 0
        self._rx = b""
        self.frames: list[Frame] = []
        self.unlocked = False

    # -- 内部 ---------------------------------------------------------------
    def _next_seq(self) -> int:
        s = self._seq & 0x0F
        self._seq = (self._seq + 1) & 0x0F
        return s

    def _get(self, q: Query) -> bytes:
        return build_frame(CID_QUERY, int(q), b"", self._next_seq())

    def _set(self, cid: Cid, payload: bytes) -> bytes:
        return build_frame(int(cid), 0, payload, self._next_seq())

    # -- 接收 ---------------------------------------------------------------
    def feed(self, chunk: bytes) -> list[Frame]:
        """喂入 notify 字节流，返回本次新解出的帧。"""
        self._rx += bytes(chunk)
        frames, rest = drain(self._rx)
        self._rx = rest
        self.frames.extend(frames)
        return frames

    def take_frames(self) -> list[Frame]:
        out = self.frames
        self.frames = []
        return out

    # -- 连接初始化 ---------------------------------------------------------
    def request_unlock(self) -> bytes:
        """连接后**必须发的第一帧**。

        特征值：ff17
        发送字节：`00 27 01 00 04 FF 02 02 34`（seq=0 示例）
        预期回包：`xx 27 02 00 03 FF 01 FF`
        验证状态：CONFIRMED（PC Bleak + Android SPP 双通道实测；不发则耳机对所有查询静默）
        来源：com.fengeek.main.i.h.setUnlock — cid=39, cmdId=-1(0xFF), buffer={2, 0x34}
              触发点 f053/F053MainFragment.java:275 `case 5:` "message: 蓝牙已连接"
        """
        return build_frame(CID_QUERY, 0xFF, bytes([0x02, 0x34]), self._next_seq())

    def request_version(self) -> bytes:
        """GET_VERSION。特征值 ff17。帧 `xx 27 01 00 02 02 00`。
        回包 payload=4B → "3.0.1.1"。CONFIRMED。"""
        return self._get(Query.VERSION)

    def request_battery(self) -> bytes:
        """GET_BATTERY。特征值 ff17。帧 `xx 27 01 00 02 01 00`。
        回包 payload=[flags, 电量%, 盒?]。CONFIRMED（读回 0x50=80%，与官方 App 一致）。"""
        return self._get(Query.BATTERY)

    def request_device_name(self) -> bytes:
        """GET_DEVICE_NAME(3)。帧 `xx 27 01 00 02 03 00`。回包是 ASCII 串（实测 "FIIL Key Max"，12 字节），**不是 MAC**。

        特征值：ff17（写）→ ff18（通知）
        验证状态：CONFIRMED
        """
        return self._get(Query.DEVICE_NAME)

    def request_key_config(self) -> bytes:
        """GET_KEY(5)。帧 `xx 27 01 00 02 05 00`。

        ⚠️ 早期误标为「GET_EARTYPE / 机型 earType」，**已证伪**：
        官方 getKey（c.java:91）用 cmdId=5，回包是 10 组「按键编号 + 类型 + 值」三元组：
        实测 `1E | 01 01 07 | 02 01 07 | 03 01 00 | 04 01 00 | ... | 0A 01 00`
        （首字节 0x1E=30 是后续字节数，不是型号；30 = 0x1E 正好等于 10 组 × 3 字节）。
        本机型上没有单字节 earType 查询命令 —— 型号只能由设备名（cmdId=3）判断。
        特征值：ff17（写）→ ff18（通知）
        验证状态：CONFIRMED（docs/evidence/unverified_cmdid_20261025_014438.txt）
        """
        return self._get(Query.KEY)

    def request_maf(self) -> bytes:
        """GET_MAF。帧 `xx 27 01 00 02 0C 00`。回包 payload[0]=MafMode。

        MafMode 五档 0..4（关闭/降噪/开放/兼听/风中）**全部实机验证可用**
        （`exp_maf_five.py` → `docs/evidence/maf_five_20261025.txt`：逐档 SET 0→4，
        每档 ACK 成功，@3.0 s 读回均与写入值一致；见 PROTOCOL_PC.md §6.4）。
        注意模式切换生效约 1.75~2.0 s（§6.5），读完不能立刻判定失败。

        特征值：ff17（写）→ ff18（通知）
        验证状态：CONFIRMED
        """
        return self._get(Query.MAF)

    def request_cinema(self) -> bytes:
        """GET_SPATIAL(24)。帧 `xx 27 01 00 02 18 00`。回包 payload[0]=0/1。
        注意：本机 Key Max 上 `GET_CINEMA(0x0B=11)` 回空帧，真正的影院位是 24。

        特征值：ff17（写）→ ff18（通知）
        验证状态：CONFIRMED
        """
        return self._get(Query.SPATIAL)

    def request_low_latency(self) -> bytes:
        """GET_LOW_LATENCY(8)。帧 `xx 27 01 00 02 08 00`。回包 payload[0]=0/1/2（本机实测只覆盖 0/1）。

        特征值：ff17（写）→ ff18（通知）
        验证状态：CONFIRMED
        """
        return self._get(Query.LOW_LATENCY)

    def request_multipoint(self) -> bytes:
        """GET_CHANGLAN(128)。帧 `xx 27 01 00 02 80 00`。回包 payload[0]=0/1。

        特征值：ff17（写）→ ff18（通知）
        验证状态：CONFIRMED
        """
        return self._get(Query.CHANGLAN)

    def request_tone_volume(self) -> bytes:
        """GET_TONE(130)。帧 `xx 27 01 00 02 82 00`。回包 `... 82 01 <volume>`。

        payload[0] 恒为 1（字段存在标志），**音量值在 payload[1]**。
        实测回包：`01 27 02 00 03 82 01 02` → payload = `01 02` → 音量档 2（音量低）。

        特征值：ff17（写）→ ff18（通知）
        验证状态：CONFIRMED（docs/evidence/new3_20261025.txt）
        来源：com.fengeek.main.i.d.c.java:251-265 getToneStatus —
              setCommandClassifyId(39), setCommandId(NetError.ERR_SSL_BAD_RECORD_MAC_ALERT)
              其中 NetError.ERR_SSL_BAD_RECORD_MAC_ALERT = **-126** → &0xFF = **0x82 = 130**
        """
        return self._get(Query.TONE)

    def request_eq(self) -> bytes:
        """GET_EQ(4)。帧 `xx 27 01 00 02 04 00`。
        回包 payload=12 字节曲线（实测 `0A 0E 08 06 04 02 00 01 FA FC FE 02`，-6..+14 的有符号值）。

        特征值：ff17（写）→ ff18（通知）
        验证状态：CONFIRMED（写入未确认，见 PROTOCOL_PC.md §12）
        """
        return self._get(Query.EQ)

    # -- 控制 ---------------------------------------------------------------
    def set_maf(self, mode: MafMode) -> bytes:
        """设置降噪模式。

        特征值：ff17
        发送字节：`xx 2C 01 00 01 <mode>`（cid=44 = SET_MAF，cmdId=0）
        预期回包：`xx 2C 02 00 01 00`（payload[0]==0 成功）
        读回确认：再发 request_maf()，payload[0] 应等于 mode
        验证状态：CONFIRMED（0..4 五档逐档写入并读回一致）
        来源：com.fengeek.main.i.f.java setMAF — setCommandClassifyId(44), setCommandId(0), buffer={status}
        """
        return self._set(Cid.SET_MAF, bytes([int(mode) & 0xFF]))

    def set_cinema(self, on: bool) -> bytes:
        """影院模式（空间音频）开关。

        特征值：ff17
        发送字节：`xx 32 01 00 01 <0/1>`（cid=50 = SET_SPATIAL，cmdId=0）
        预期回包：`xx 32 02 00 01 00`
        读回确认：request_cinema() → payload[0] == 1/0
        验证状态：CONFIRMED（写入 1 → 读回 1；写回 0 → 读回 0）
        来源：com.fengeek.main.i.c.java:504-518 setSpatialAudio
        """
        return self._set(Cid.SET_SPATIAL, bytes([1 if on else 0]))

    def set_low_latency(self, level: int) -> bytes:
        """低延迟档位。

        特征值：ff17
        发送字节：`xx 25 01 00 01 <level>`（cid=37 = SET_LATENCY，cmdId=0）
        预期回包：`xx 25 02 00 01 <00=成功 / 01=耳机拒绝>`

        ⚠️ 机型差异（2026-10-25 实测，docs/evidence/ll4_20261025.txt）：
          * GAIA 老机型：0=关闭 1=游戏 2=音乐 3=视频
          * **Mate 机型（本机 Key Max）：只有 0/1**。
            SET 2 → ACK `05 25 02 00 01 01`（payload=01 = 失败）
            SET 3 → ACK `07 25 02 00 01 01`（payload=01 = 失败）
            读回恒为 1。所以上层必须检查 ACK 的 payload[0]，
            不能只发不看 —— 否则界面会假装切到了「音乐/视频」。
        来源：com.fengeek.main.i.f.java setLowDelay — setCommandClassifyId(37), setCommandId(0), buffer={z}
        """
        return self._set(Cid.SET_LATENCY, bytes([level & 0xFF]))

    def set_tone_volume(self, volume: ToneVolume | int) -> bytes:
        """设置语音提示音量（内置提示音 + 语音播报）。

        特征值：ff17
        发送字节：`xx 01 01 00 03 03 01 <volume>`（cid=1 = SET_PARAM，cmdId=3）
        预期回包：`xx 01 02 00 03 03 01 00`（末字节 00 = 成功）
        读回确认：request_tone_volume() → payload[1] == volume
        验证状态：CONFIRMED（2026-10-25 实测 2→1→0 三档逐档写入，
                  ACK 全成功且读回全一致，见 docs/evidence/new3_20261025.txt）
        来源：com.fengeek.main.i.d.f.java:711-726 setTone —
              setCommandClassifyId(1), setCommandId(3), setBuffer({b2})
        """
        v = int(volume) & 0xFF
        return build_frame(int(Cid.SET_PARAM), 3, bytes([v]), self._next_seq())

    def set_multipoint(self, on: bool) -> bytes:
        """蓝牙畅连（多点）开关。

        特征值：ff17
        发送字节：`xx 01 01 00 03 01 01 <0/1>`（cid=1 = SET_PARAM，cmdId=1，payload 只有 1 字节）
        预期回包：`xx 01 02 00 03 01 01 00`
        ⚠️ 切换后耳机 BLE 会短暂断链（主从切换），需等 2~3 秒后重连再 setUnlock。
        验证状态：CONFIRMED（1→0 读回 0；0→1 读回 1）
        来源：com.fengeek.main.i.f.java setChangLan — 实机抓包 docs/evidence/set4_20261025_003607.txt

        早期实现误把 `01 01` 也塞进 payload，组出 10 字节帧 `.. 05 01 03 00 01 01`，
        与实机 8 字节帧不符 —— 已按实测修正。
        """
        return build_frame(int(Cid.SET_PARAM), 1, bytes([1 if on else 0]), self._next_seq())

    def set_media_play_pause(self) -> bytes:
        """播放/暂停。特征值 ff17。帧 `xx 21 01 00 03 02 01 01`。CONFIRMED。"""
        return build_frame(int(Cid.SET_MUSIC), 2, bytes([1, 0x01, 0x01]), self._next_seq())

    def set_media_next(self) -> bytes:
        """下一曲。特征值 ff17。帧 `xx 21 01 00 02 05 00`。CONFIRMED。"""
        return build_frame(int(Cid.SET_MUSIC), 5, b"", self._next_seq())

    def set_media_prev(self) -> bytes:
        """上一曲。特征值 ff17。帧 `xx 21 01 00 02 04 00`。CONFIRMED。"""
        return build_frame(int(Cid.SET_MUSIC), 4, b"", self._next_seq())

    def set_eq(self, bands: bytes) -> bytes:
        """写自定义 EQ（12 字节）。

        特征值：ff17
        发送字节：`xx 20 01 00 0C <12 bytes>`（cid=32 = SET_EQ，cmdId=0）
        预期回包：`xx 20 02 00 01 01`
        ⚠️ **回包成功但读回不变** → 本机 Key Max 上未生效。状态 PARTIAL，不放进语义化按钮。
        来源：com.fengeek.main.i.f.java setEQ
        """
        if len(bands) != 12:
            raise ValueError("EQ 必须是 12 字节")
        return self._set(Cid.SET_EQ, bytes(bands))

    # -- 静态解析辅助 -------------------------------------------------------
    @staticmethod
    def battery(frame: Frame) -> BatteryState:
        return BatteryState.parse(frame.payload)

    @staticmethod
    def maf(frame: Frame) -> MafMode | None:
        v = parse_uint(frame.payload)
        try:
            return MafMode(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def tone_volume(frame: Frame) -> ToneVolume | None:
        """从 GET_TONE 回包里取音量档位。

        回包 payload 实测为 `01 <volume>`：payload[0] 是字段存在标志（恒 1），
        音量值在 payload[1]。若只有 1 字节则退回读 payload[0]（容错）。
        """
        payload = frame.payload
        if len(payload) >= 2:
            v = payload[1]
        elif len(payload) == 1:
            v = payload[0]
        else:
            return None
        try:
            return ToneVolume(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
