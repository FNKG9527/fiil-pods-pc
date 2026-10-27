# PROTOCOL_PC.md — FIIL 耳机 BLE GATT 协议规格（PC 探针实测版）

> 本文件所有 UUID 与字节都来自 **本机实机抓包**，不是推测。
> 每条结论后面标注证据文件（`out/*.txt`）或 FIIL+ 官方反编译源码行号。
> 未在真机上验证的，一律标 `未确认`，不写入控制代码。

- **被测设备**：FIIL Key Max，BLE 地址 `B0:F1:A3:00:00:01`
- **固件版本**：3.0.1.1（`GET_VERSION` → payload `03 00 01 01`）
- **测试时间**：2026-10-25 00:16 — 01:50
- **测试平台**：Windows 11 + Python 3.12.6 + bleak（WinRT 后端）
- **探针程序**：`app.py`（GUI）/ `ble_worker.py`（BLE 线程）/ `fiil_protocol.py`（协议层）
- **单元自检**：`python -X utf8 test_protocol.py` → **67 通过 / 0 失败**

---

## 0. 结论速览

| 项目 | 结论 |
|---|---|
| 传输层 | **BLE GATT**，服务 `0000fdb3`，写 `ff17`，通知 `ff18`（`ff16` 可写但耳机不响应） |
| 连接后必须做的事 | **先发 `setUnlock`**，否则耳机对所有命令静默 |
| 帧格式 | `seq cid type 00 [lenField] [cmdId] [payloadLen] payload`（6~N 字节，见 §2） |
| GET 语义 | `cid=39` 固定，`cmdId` = 查询码（18 条全部实机验证） |
| SET 语义 | `cid` = 命令族编号，**`cmdId=0`**，长度字段直接是 payloadLen |
| seq | **不能用来匹配请求/响应**（耳机用自己的滚动计数器，见 §2.5） |
| type | 必须同时接受 `0x01/0x02/0x03`，只认 `0x01` 会丢掉全部回包 |
| SET 生效延迟 | MAF 约 **1.75~2.0 s**；影院 **0.5 s**；低延迟 **0.45 s**（靠 NOTIFY 上报，GET 轮询追不上） |
| 状态回读 | 以 `cid=40`（NOTIFY）为准，不能依赖 SET 的 ACK |
| MAF 档位 | 源码定义 5 档，**本机实测 0/1/2/3/4 全部可用**（见 §6.4） |
| 低延迟档位 | GAIA 四档（关/游戏/音乐/视频）；**Mate 机型（本机 Key Max）只有 0/1**，SET 2/3 被耳机 ACK 明确拒绝（见 §8.2） |
| 蓝牙畅连 | GET `cmdId=128` / SET `cid=1 cmdId=1`；**切换后 BLE 立即断链**，需重连并重做 `setUnlock`（见 §9） |
| 语音提示音量 | GET `cmdId=130` / SET `cid=1 cmdId=3`；**线值反序**：0 高 / 1 中 / 2 低 / 3 静音（见 §8bis） |
| SET 成功判据 | 6 字节短帧 ACK `xx cc 02 00 01 <00 成功 / 01 拒绝>` —— 必须检查，否则界面会假装成功 |

---

## 1. Service / Characteristic UUID 表（已确认）

实测枚举结果，证据：`docs/evidence/gatt_20261025_001610.txt`

```
Service 0000fdb3-0000-1000-8000-00805f9b34fb  [Vendor specific]
  Char 0000ff18-0000-1000-8000-00805f9b34fb handle=2 props=[notify,read]
    Desc 00002902-0000-1000-8000-00805f9b34fb handle=4      ← CCCD
  Char 0000ff16-0000-1000-8000-00805f9b34fb handle=5 props=[write,read]
  Char 0000ff17-0000-1000-8000-00805f9b34fb handle=7 props=[write-without-response,write]
```

| 角色 | UUID | handle | 属性 | 说明 |
|---|---|---|---|---|
| Service | `0000fdb3-0000-1000-8000-00805f9b34fb` | — | — | 唯一自定义服务 |
| **写特征** | `0000ff17-0000-1000-8000-00805f9b34fb` | 7 | write / write-no-rsp | **所有命令都发这里** |
| **通知特征** | `0000ff18-0000-1000-8000-00805f9b34fb` | 2 | notify / read | **所有回包都从这里来** |
| CCCD | `00002902-0000-1000-8000-00805f9b34fb` | 4 | — | bleak 的 `start_notify` 会自动处理 |
| 干扰项 | `0000ff16-0000-1000-8000-00805f9b34fb` | 5 | write / read | **可写但耳机完全无视**（`docs/evidence/log_unlock16_20261025_001943.txt` 无任何回包） |

### 1.1 三条实测踩坑（写进代码注释里了）

1. **网上资料普遍把 ff16 写成写特征值 —— 在本机型上是错的。** 往 ff16 写 `setUnlock` 无任何响应；同样字节写 ff17 立即回包。
2. **写入用 `response=False`（Write Without Response）。** 用 Write With Response 在高频发送时偶发 `GATT_BUSY` / 超时。
3. **CCCD 不写也能收到 notify**（Android 侧同样实测）。但统一起见仍建议 `start_notify`。

### 1.2 MTU

实测 `client.mtu_size = 512`。EQ 12 字节等长帧一次写完无需分包。但**接收侧必须做长度重组**——耳机可能把一个逻辑帧拆成多次 notify（见 §2.4）。

### 1.3 设备发现：FIIL 在 BLE 广播里通常不带名字

这是**扫描列表只显示 MAC、认不出是哪台耳机**的原因，不是程序 bug。

实测证据（本机 FIIL Key Max）：

| 项 | 值 |
|---|---|
| 经典蓝牙 BD_ADDR（配对/系统里用的） | `B0:F1:A3:00:00:02` |
| BLE 广播地址（Bleak 扫到的） | `B0:F1:A3:00:00:01` |
| BLE 广播包里的 `local_name` | **空**（`dev.name is None`） |

即：FIIL 是双模设备，主通道在经典蓝牙，BLE 侧只暴露一个**不带名字**的广播，
而且 **BLE 地址与经典地址只差最后一个字节**（`…:00:02` vs `…:00:01`）。

`ble_worker._scan()` 的名称解析因此按三级回退：

1. `dev.name` / `adv.local_name` —— 广播自带的名字（最准，但 FIIL 通常为空）
2. Windows 配对/历史缓存 —— `Get-PnpDevice -Class Bluetooth` 里 `FriendlyName` + `InstanceId` 的 `DEV_xxxxxxxxxxxx`，转成 `MAC → 名称` 表（结果缓存 30 s）
3. **前缀回退** —— 缓存表里按前 5 段（`B0:F1:A3:00:00`）找同一台设备，**只有唯一命中才采用**（避免同 OUI 下不同型号张冠李戴）；仍无则显示「FIIL 设备（型号待连接后读取）」

FIIL 机型的 OUI 前缀（逆向自 `FiilConfig.FIIL_OUI_PREFIXES`）：`B0:F1:A3`、`FF:F1:A3`。
命中 OUI 或名字含 `FIIL` 的设备在列表里打 `★` 并排到最前，**所以即使没名字也不会点错**。

连上之后 `GET_DEVICE_NAME`（cmdId=3）返回真实名（如 `FIIL Key Max`），
GUI 会就地改写扫描列表那一项，第二次扫描起直接显示真名。

#### 1.3.1 更常见的情况：耳机根本不在广播里

比「广播没名字」更常遇到的是**扫描列表里连这个地址都没有**。实测复现：

| 时间点 | 现象 |
|---|---|
| 耳机与 PC 未连接 | 扫描能扫到 `B0:F1:A3:00:00:01`，名称靠配对缓存回退 |
| 耳机已被 PC 经典蓝牙占用（A2DP 在放音） | 连续 5 次扫描（8~25 s），共 107~136 个设备，**FIIL OUI 命中 0 条**；按 `ff17` / `ff18` / `fdb3` 服务 UUID 指纹也 0 条 |

原因：FIIL 是双模耳机，一旦在经典蓝牙链路上活动，**BLE 侧就不再发可连接广播**。
但它仍然可以被 BLE **按地址直连**（WinRT 支持对已知地址 `connectGatt`）。

因此程序提供两条兜底（`ble_worker.py`）：

1. **已知设备表 `known_devices.json`** —— 每次连接成功先把 BLE 地址落盘（此时还不知道型号），
   `GET_DEVICE_NAME` 回包后再补真名。**空名字写入不会覆盖已学到的型号**（有单测钉死）。
   `_scan()` 扫完后把表里「本次没扫到」的地址补成一行 `★`，用户仍可直接点连接。
2. **GUI「直连地址」输入框** —— 启动时自动预填上次连过的地址，一键连接，完全不依赖扫描。
   输入会规范化，`b0f1a3000001` / `b0-f1-a3-00-00-01` 这类写法都接受。

> 注意：不能拿配对缓存里的经典蓝牙地址直连 BLE —— 两者不是同一个地址
> （本机 `…:00:02` 是经典，`…:00:01` 是 BLE）。所以只有**真正连上过的 BLE 地址**才进已知设备表。


---

## 2. 帧格式（已确认）

### 2.1 总布局

```
偏移  0     1     2      3       4         5        6          7..
     seq   cid   type   00   lenField   cmdId   payloadLen   payload
```

- **seq**：1 字节，低 4 位滚动计数器（0..15）。发送时自增即可。
- **cid**：1 字节，命令族编号。GET 固定 `39 (0x27)`。
- **type**：`0x01`=REQUEST（主机→耳机） / `0x02`=RESPONSE（耳机应答） / `0x03`=NOTIFY（耳机主动推送）
- **byte[3]**：保留，必须 `0x00`，非 0 视为非法帧。
- **lenField**：**关键字段**，决定整帧长度，取值见 §2.2。
- **总长度 = 5 + lenField**（`lenField == 1` 时总长 6，是特例）。

来源：`com/fengeek/main/i/b.java:237-262 writeMate()`。

### 2.2 lenField 的三种情况（这是最容易写错的地方）

| lenField | 总长 | 结构 | 典型用途 |
|---|---|---|---|
| `0` | 5 | `seq cid type 00 00` | **耳机不认识这条命令**时回的空帧 |
| `1` | 6 | `seq cid type 00 01 <ok>` | **SET 命令的应答**（无 cmdId，第 6 字节就是 payload[0]） |
| `>=2` | `5+lenField` | `seq cid type 00 len cmd plen payload…` | 所有 GET 应答、所有请求 |

**`lenField == 1` 是最隐蔽的坑**：它看起来像「长度 1 的空帧」，但第 6 字节是**成功标志**（`0x00`=成功）。

> 实测证据：SET_MAF 回 `02 2C 02 00 01 00` —— 6 字节，`lenField=1`，`payload[0]=0x00` 成功。
> 早期实现按 `lenField<2 → 5 字节空帧` 切帧，把成功字节整个吞掉，导致所有 SET 都误判失败。**已修正**，`test_protocol.py` 有 3 条用例钉住。

### 2.3 组帧示例（全部来自实机）

```
setUnlock        00 27 01 00 04 FF 02 02 34        # cid=39 cmd=0xFF payload=02 34
GET_BATTERY      02 27 01 00 02 01 00              # cid=39 cmd=1  无 payload
SET_MAF=1        0A 2C 01 00 01 01                 # cid=44 cmdId=0 payload=01
SET_SPATIAL=1    0B 32 01 00 01 01                 # cid=50 cmdId=0 payload=01
SET_LATENCY=1    0C 25 01 00 01 01                 # cid=37 cmdId=0 payload=01
SET_CHANGLAN=1   0D 01 01 00 03 01 01 01           # cid=1  cmdId=1 payload=01
```

注意 `SET_CHANGLAN` 是**唯一带 cmdId 的 SET**（cmdId=1）。

### 2.4 接收侧必须做流式重组

三个真实约束（都有实测用例）：

1. 帧可能被拆到多次 notify（**半帧必须保留**，不能丢）
2. 一次 notify 可能含多帧（**粘包**）
3. 收到垃圾字节要**逐字节重同步**，直到找到合法帧头（`type ∈{1,2,3}` 且 `byte[3]==0`）

`drain()` 的三分支实现：`lenField=0→need 5`、`=1→need 6`、`>=2→need 5+lenField`；`avail < need` 时 **break 保留**，帧头非法才 `pos += 1`。

### 2.5 ⚠️ seq 不是请求关联号（重要）

**实测**：TX `seq=04` → RX `seq=05`。耳机会**用自己的滚动计数器**独立递增，且 RESPONSE 与 NOTIFY **共用同一个计数器**。

```
[TX] GET_MAF @2.0s  04 27 01 00 02 0C 00
[RX]                05 27 02 00 03 0C 01 00      ← 不是 04
[TX] GET_MAF @3.0s  05 27 01 00 02 0C 00
[RX]                06 27 02 00 03 0C 01 00
```

**结论：不能用 seq 做请求-响应匹配。** 只能按 `cid + cmdId` 语义分发。

这也和官方实现一致：`com.fengeek.main.i.d.*` 的 stateHandler 对「cid/cmd 与待处理队列不匹配」的回包**照样走同一条状态更新路径**——即 cid=40 的主动上报和 GET 的应答是同等可信的状态来源。

### 2.6 type 三值的实测含义

| type | 含义 | 例 |
|---|---|---|
| `0x01` | REQUEST，主机发出 | `0A 27 01 00 02 0C 00` |
| `0x02` | RESPONSE，耳机对某条命令的应答 | `0A 27 02 00 03 0C 01 01` |
| `0x03` | NOTIFY，耳机**主动**推送（cid 恒 40） | `0B 28 03 00 03 0C 01 00` |

旧实现把 `byte[2]` 写死成 `0x01` 才解析 → **所有回包都被丢弃**。这是导致「连上了但状态永远收不到」的直接原因。**已修正**。

---

## 3. GET 命令全表（18 条 + 握手，全部实机验证）

证据：`docs/evidence/unverified_cmdid_20261025_014438.txt`、`docs/evidence/table_20261025_002133.txt`

| cmdId | 名称 | 源码方法（`com/fengeek/main/i/d/c.java`） | 实测回包 payload | 解析 |
|---|---|---|---|---|
| 1 | 电量 | `getElectricity` :75 | `00 4D 00` | F046 单电池，`payload[1]=0x4D=77%`（见 §5.2） |
| 2 | 版本 | `getVersion` :267 | `03 00 01 01` | 固件 3.0.1.1 |
| 3 | 设备名 | （实测） | `46 49 49 4C 20 4B 65 79 20 4D 61 78` | **ASCII "FIIL Key Max"**（12 字节），不是 MAC |
| 4 | EQ | `getEQStatus` :59 | `0A 0E 08 06 04 02 00 01 FA FC FE 02` | 12 字节有符号曲线 |
| 5 | 按键配置 | `getKey` :91 | `01 01 07 02 01 07 03 01 00 …`（30 字节） | 10 组「键号+类型+值」 |
| 7 | 播放状态 | `getMusicStatus` :187 | `00` | 0=暂停 1=播放 |
| 8 | 低延迟 | `getLowDelayStatus` :155 | `00` | 0=关 1=开 |
| 9 | 佩戴检测 | （无对应 getter） | **空帧 `xx 27 02 00 00`** | **本机型不支持** |
| 12 | MAF 降噪 | `getMAFStatus` :171 | `01` | 0..4 五态 |
| 24 | 影院/空间音频 | `getSpatialAudioStatus` :235 | `00` | 0=关 1=开 |
| 128 | 畅连（多点） | `getChangLanStatus` :27 | `01` | 0=关 1=开 |
| 130 | 音效 | `getToneStatus` :251（NetError -126） | `02` | 音效档位 |
| 132 | PID | `getPID` :203 | `00 2E 01` | 产品标识 |
| 138 | 左耳 UUID | `getLeftHeadsetUUID` :139（NetError -118） | `10 42 50 32 36 32 30 35 12 00 3B 51 45 06 20 BB 78` | 16 字节 |
| 139 | 右耳 UUID | `getRightHeadsetUUID` :219（NetError -117） | 同上 | 16 字节 |
| 142 | LDAC 解锁 | `getLdacUnlockStatus` :123（NetError -114） | `01` | — |
| 143 | LDAC 开关 | `getLdacSwitchStatus` :107（NetError -113） | `00` | — |
| 144 | 编码格式 | `getCodecStatus` :43（NetError -112） | `02` | 0/1/2 编码档 |
| 255 | 解锁握手 | `setUnlock`（cid=39 cmdId=-1） | `FF` | 见 §4 |

### 3.1 ⚠️ 一个已经证伪的误解：cmdId=5 **不是** earType

早期把 `cmdId=5` 标成「GET_EARTYPE / 机型 earType」，因为回包首字节 `0x1E = 30` 看起来像个型号编号。

**已证伪**：官方 `getKey`（`c.java:91`）用的就是 `cmdId=5`，语义是**触摸按键配置表**。回包结构是：

```
payloadLen = 0x1E = 30 字节（帧头第 6 字节，不是 payload 内容）
payload = 01 01 07 | 02 01 07 | 03 01 00 | 04 01 00 | 05 01 00
        | 06 01 00 | 07 01 00 | 08 01 00 | 09 01 00 | 0A 01 00
```

共 10 组「按键编号 + 类型 + 值」三元组，共 30 字节 = payloadLen。**没有任何 earType 字段。**

**本机型上没有单字节 earType 查询命令。** 型号只能由设备名（cmdId=3）或 BLE 广播名判断。

> 手机模块侧的教训：`FiilMate` 里如果把 cid=39 cmd=5 当 earType 用，会拿到 30 而误判成 CC2。**必须改用设备名匹配。**

---

## 4. 连接初始化流程（已确认）

### 4.1 必须的握手

```
1. connect GATT
2. start_notify(ff18)
3. 发 setUnlock ──► 00 27 01 00 04 FF 02 02 34
   ◄──            00 27 02 00 03 FF 01 FF
4. 之后才能发任何 GET/SET
```

**不发 setUnlock，耳机对所有命令完全静默。** 这是最关键的初始化动作。

证据：`docs/evidence/fiil_log_20261025_155218.txt` 中未解锁时的静默段 vs `docs/evidence/log_unlock17_20261025_001932.txt` 立即回包。

### 4.2 setUnlock 的来源

`com/fengeek/main/i/d/h.java:712-727`：

```java
public void setUnlock(infoListener) {
    c.a aVar = new c.a();
    aVar.setCommandClassifyId(39);          // cid = 39
    aVar.setCommandId(-1);                  // cmdId = -1 → 0xFF
    aVar.setBuffer(new byte[]{2, 52});      // payload = 02 34  (大写 0x34 = 十进 52)
    a.getInstance().addCommand(aVar);
}
```

触发时机：`F053MainFragment.java:275-279` 的 `case 5:`（"message: 蓝牙已连接"）→ 连接成功后**立即**发。

### 4.3 推荐的完整初始化序列

```
setUnlock
→ GET_VERSION(2)      确认通信正常
→ GET_BATTERY(1)
→ GET_MAF(12)
→ GET_SPATIAL(24)
→ GET_LOW_LATENCY(8)
→ GET_CHANGLAN(128)
→ GET_TONE(130)
```

**每条之间建议 ≥250 ms**。

> ⚠️ 这一条的证据强度要如实说明：**没有完全证实是时序问题**。
>
> 观察到：某次连接成功后，`GET_LOW_LATENCY` / `GET_CHANGLAN` / `GET_TONE` 三条全部无回包，
> 而同一次连接里 `GET_BATTERY` / `GET_MAF` 正常；紧接着下一次连接直接失败，
> 再之后耳机从 BLE 扫描里彻底消失（`BleakDeviceNotFoundError`）。也就是说，
> **「0/3」更可能是耳机正在离线的前兆，而不是发得太密**。
>
> 已做的防御（不依赖上述判断成立）：
> 1. 间隔从 120 ms 提到 250 ms —— 更保守，代价只是连接后慢 1 秒；
> 2. 握手结束后对缺失项**补读一次**（`ble_worker.py` 的 `_got_ll/_got_mp/_got_tone`）。
>
> 状态：**补读逻辑已由 `test_worker_ack.py` 离线验证（16/16）；250 ms 间隔尚无实机复核**——
> 复核时耳机已离线。下次连上真机需要确认这两项。

### 4.4 断连重连

断连后**必须重新 setUnlock**（耳机会重新上锁）。重连后 `start_notify` 也要重做。

**畅连（多点）切换是特例**：`SET_CHANGLAN` 会让耳机立刻断开 BLE（主从角色交换），
重连后同样必须重做 `setUnlock` —— `test_new3_live.py` 已实测确认。程序必须自动重连，
否则用户会以为「开了畅连就连不上耳机了」。

---

## 5. 电量解析规则（已确认）

### 5.1 帧

```
TX  xx 27 01 00 02 01 00
RX  xx 27 02 00 05 01 03 00 4D 00
                    │  │  │  │  │  └ payload[2] = 0x00
                    │  │  │  │  └── payload[1] = 0x4D = 77%   ← 本机型有效值
                    │  │  │  └──── payload[0] = 0x00
                    │  │  └─────── payloadLen = 3
                    │  └────────── cmdId = 1
                    └───────────── lenField = 5
```

### 5.2 按机型分两种语义（**不是「只有 payload[1] 有效」**）

同一帧 `00 4D 00` 在不同机型上含义不同，必须按 FIIL 型号（earType）分流：

| 机型族（earType） | 官方实现 | 语义 |
|---|---|---|
| **F046（earType 46）/ F042（42）** | `F046HomeFragment:623` → `m(buff[1])`<br>`F042HomeFragment:440` → `D(buff[1])` | **单电池**，只读 `payload[1]`，整机一个电量 |
| F045 / F047 / F049 / F051 / F053 | `setElectricity` → `o(buff[0]); r(buff[1]); m(buff[2])` | **三值**：`payload[0]=左耳`、`payload[1]=右耳`、`payload[2]=充电盒` |
| F048 | `buff[0]==0 \|\| buff[1]==0` 判左右耳在线，再 `getEarSurplus()` | 左右耳两值（盒电量另查） |

**本机 Key Max 是 F046 → 单电池**：payload `00 4D 00` 应显示为「耳机 77%」，
**不能显示成「右耳 77%」**——那是三耳机型的语义，会误导用户以为只有右耳有电。
多轮复测数值会变（另一轮读到 `00 4C 00` = 76%），符合真实电量波动
（`exp_battery.py` → `docs/evidence/battery_20261025.txt`：20 次采样稳定 `00 4D 00`）。

### 5.2.1 短帧布局（earType 未知时的兜底）

| payload 长度 | 布局 |
|---|---|
| 3 | 按上表分流；earType 未知时按 `[左][右][盒]` 解释 |
| 2 | `[左][右]` |
| 1 | `[右]` |

### 5.3 无效值处理

`0` 与 `255 (0xFF)` 都视为「无数据」，显示 `--`。**不要显示 0% 或 255%**。必须对短帧、空帧做防御，单帧异常不能崩。

### 5.4 官方依据

- `jadx_out/sources/com/fengeek/main/f046/fragment/F046HomeFragment.java:623`
  `public void setElectricity(byte[] buff) { m(buff[1]); }` —— 单电池
- `...\f045\fragment\F045HomeFragment.java:546`
  `o(buff[0]); r(buff[1]); m(buff[2]);` —— 左/右/盒
- `...\f047\...\F047HomeFragment.java:390`、`...\f049\...\F049HomeFragment.java:457`、
  `...\f051\...\F051HomeFragment.java:723`、`...\f053\...\F053HomeFragment.java:722` —— 均为三值
- `...\f042\fragment\F042HomeFragment.java:440` —— `buff[0] != 0` 判有效后读 `buff[1]`
- 路由：`...\f002\MainActivity.java:1032-1049` `case 42→F042 / 46→F046 / 45→F045 / 47→F047 / 48→F048 / 49→F049 / 51→F051 / 53→F053`

---

## 6. MAF 降噪模式（**五态全部实机验证通过**）

### 6.1 枚举

| 值 | 含义 | 文案（FIIL+ `fragment_f46_style.xml` 原文） | 本机实测 |
|---|---|---|---|
| 0 | 关闭 | 关闭 | ✓ 写入回读一致 |
| 1 | 降噪 | 降噪 | ✓ 写入回读一致 |
| 2 | 开放 | 开放 | ✓ 写入回读一致 |
| 3 | 兼听 | 兼听 | ✓ 写入回读一致 |
| 4 | 风中 | 风中 | ✓ 写入回读一致 |

**五档全部可用**（`exp_maf_five.py` → `docs/evidence/maf_five_20261025.txt`：逐档 SET 0→4，每档 ACK 均 `OK`，
@3.0 s 读回均等于写入值）。早期「只覆盖 0/1/2」的判断是**采样偏差**——当时只被动读取耳机上报值，
没有主动下发 3/4，不能据此推断固件不支持。

> ⚠️ 注意与目标描述的对齐：目标写「降噪 / **自适应** / 风中 / 监听 / 关闭」，而 FIIL+ 原文是
> 「关闭 / 降噪 / 开放 / **兼听** / 风中」。即目标里的「自适应」在本协议里**不存在对应线值**，
> 它实际是 4 个独立的「自适应开关」（cid=1, cmdId=14/15/16/17），不是 MAF 的第 6 档；
> 目标里的「监听」= FIIL 原文「兼听」(3)。

### 6.2 读

```
TX  xx 27 01 00 02 0C 00
RX  xx 27 02 00 03 0C 01 <mode>
```

### 6.3 写

```
TX  xx 2C 01 00 01 <mode>          # cid=44, cmdId=0, payload=mode
RX  xx 2C 02 00 01 00              # 6 字节，payload[0]=00 表示成功
```

### 6.4 写入矩阵（全五态实测）

`exp_maf_five.py` 输出（设备 `B0:F1:A3:00:00:01`，固件 3.0.1.1）：

| 写入 | ACK | @0.6 s | @2.0 s | @3.0 s | 判定 |
|---|---|---|---|---|---|
| 0 关闭 | OK | 0 | 0 | 0 | ✓ |
| 1 降噪 | OK | 0 | 1 | 1 | ✓ |
| 2 开放 | OK | 1 | 2 | 2 | ✓ |
| 3 兼听 | OK | 2 | 3 | 3 | ✓ |
| 4 风中 | OK | 3 | 4 | 4 | ✓ |

模式切换**必须逐档递增**（0→1→2→3→4 才能测到，跳档未测）。
注意 @0.6 s 读到的仍是**上一档**，印证 §6.5 的延迟结论。

### 6.5 ⚠️ 生效延迟约 1.75–2.0 秒

实测（`exp_maf_latency.py`，400 ms 轮询 + NOTIFY 计时）：

```
SET_MAF=2  →  ACK 立即回 (payload=00)
GET @0.42s  →  1  ✗（旧值）
GET @1.09s  →  1  ✗（旧值）
GET @1.75s  →  2  ✓  ← GET 首次读到新值
NOTIFY      →  1.95s  ← 耳机主动上报新值
GET @3.0s   →  2  ✓
```

早期记录为「1.5 s」是采样间隔太粗造成的低估，**真实值落在 1.7–2.0 s**。

**因此：写完后不能在同一个 tick 里立刻读回判断成功。** 正确做法：
1. 以 ACK 的 `payload[0]==0` 判定**命令被接受**
2. 以 `cid=40` 的 NOTIFY 或 **≥2 s 后的 GET** 判定**状态已生效**
3. 2 s 内读回旧值 ≠ 失败

### 6.6 已验证的写入矩阵

`docs/evidence/set_matrix_20261025_015030.txt`：

| 项目 | ACK | @0.3s | @2.0s | @3.0s |
|---|---|---|---|---|
| MAF 降噪 | OK | 旧值 | 新值 | 新值 |
| 影院 | OK | 新值 | 新值 | 新值 |
| 低延迟 | OK | 新值 | 新值 | 新值 |

---

## 7. 影院模式（空间音频）

### 7.1 读

```
TX  xx 27 01 00 02 18 00          # cmdId=24 (0x18)
RX  xx 27 02 00 03 18 01 <0/1>
```

### 7.2 写

```
TX  xx 32 01 00 01 <0/1>          # cid=50 (0x32), cmdId=0
RX  xx 32 02 00 01 00
```

来源：`com/fengeek/main/i/d/c.java:504-518 setSpatialAudio`。

### 7.3 ⚠️ `cmdId=11 (0x0B)` 是陷阱

网上/部分资料把影院模式写成 `cmdId=0x0B`。**实测回空帧 `xx 27 02 00 00`（不支持）**。真正的影院位是 **24 (0x18)**。

### 7.4 生效速度（实测 0.5 s）

精确计时实验（`exp_cinema_latency`）：下发 `SET_SPATIAL=1` 后 250 ms 轮询 + 220 ms 读，**GET 首次读到新值为 0.5 s**。
比 MAF（1.75 s）快得多，但**不是「0.3 s 内」**——早期用 500 ms 单次采样得出的「≤0.3 s」是分辨率不足的低估。

**影院切换全程没有 cid=40 主动推送**（NOTIFY=None），因此影院**只能靠 GET 读回确认**，不能用 NOTIFY 回读。

---

## 8. 低延迟模式

### 8.1 读 / 写

```
TX  xx 27 01 00 02 08 00          # GET, cmdId=8
RX  xx 27 02 00 03 08 01 <level>

TX  xx 25 01 00 01 <level>        # SET, cid=37 (0x25), cmdId=0
RX  xx 25 02 00 01 00
```

### 8.2 档位语义（⚠️ 机型分流：这是本文档最容易踩错的一节）

| 值 | GAIA 老机型 | **Mate 机型（本机 Key Max / earType 46）** |
|---|---|---|
| 0 | 关闭 | 关闭 |
| 1 | 游戏 | 开启（等价于「游戏」） |
| 2 | 音乐 | **耳机拒绝** |
| 3 | 视频 | **耳机拒绝** |

**Mate 机型只有 0/1 两档，这一条有硬证据 —— 不是「还没验证」，是耳机明确回绝了：**

```
TX  xx 25 01 00 01 02        # SET_LOW_LATENCY=2（音乐）
RX  xx 25 02 00 01 01        # ACK payload[0] = 0x01 → 失败
TX  xx 25 01 00 01 03        # SET_LOW_LATENCY=3（视频）
RX  xx 25 02 00 01 01        # ACK payload[0] = 0x01 → 失败
TX  xx 27 01 00 02 08 00     # GET 复核
RX  xx 27 02 00 03 08 01 01  # 读回仍是 1 → 确实没生效
```

证据文件：`docs/evidence/ll4_20261025.txt`。

**所以上层实现必须检查 SET 的 ACK 字节**，不能「发完就当成功」——否则界面会假装切到了「音乐 / 视频」，而耳机根本没动。本仓库 `ble_worker.py` 的 `_interpret_set_ack()` 就是干这件事的；GUI 收到 `setack(ok=False)` 会给出「Mate 机型只支持两档」的明确提示。

> 另注：早期文档写「2/3 未确认、不进语义化按钮」是保守但正确的做法。
> 现在依据 ACK 失败证据升级为「Mate 明确不支持」；GAIA 机型仍保留四档，
> GUI 在读到 2/3 时会自动补出「音乐 / 视频」按钮（`app.py:_ensure_ll_buttons`）。

### 8.3 生效速度（实测 0.45 s，靠 NOTIFY 上报）

精确计时实验：下发 `SET_LOW_LATENCY=1` 后，**cid=40 NOTIFY 在 0.45 s 上报新值**。
但同一轮的 GET 轮询**始终读到旧值**（GET 首次读到新值 = None）——说明低延迟**必须靠 NOTIFY 回读**，
GET 轮询在这个时间窗里追不上（耳机侧状态切换与 GET 应答存在竞态）。

**实践建议**：低延迟写后判成功，优先看 `cid=40 cmd=8` 的 NOTIFY，其次等 ≥1 s 再 GET。
本仓库 GUI 采用后者（`after(1200, ...)`），实测可稳定读到新值。

---

## 8bis. 语音提示音量（Tone Volume）

内置提示音与语音播报的**音量档位**，与媒体音量无关。

### 8bis.1 读 / 写

```
TX  xx 27 01 00 02 82 00          # GET, cmdId=130 (0x82)
RX  xx 27 02 00 03 82 01 <level>  # payload[0]=01 是「字段存在」标志，音量在 payload[1]

TX  xx 01 01 00 03 03 01 <level>  # SET, cid=1, cmdId=3, payloadLen=1
RX  xx 01 02 00 03 03 01 00       # 末字节 00 = 成功
```

### 8bis.2 ⚠️ 线值与文案是**反序**的

| 线值 | 界面文案 |
|---|---|
| 0 | 音量高（默认） |
| 1 | 音量中 |
| 2 | 音量低 |
| 3 | 静音 |

**别按「数值越大越响」的直觉实现**。依据：FIIL+ `res/values/arrays.xml` 的
`f43_select_operate_volume`（音量高 / 音量中 / 音量低 / 静音），以及
`com/fengeek/main/f046/fragment/F046HomeFragment.java:524-538` `setVolume(int)`：
`==3 → "静音"`、`==2 → "提示音-低"`、`==1 → "提示音-中"`、`==0 → "提示音-高"`。

### 8bis.3 实测记录

2026-10-25 实机逐档写入 2→1→0，**三档 ACK 全部成功、读回全部一致**。证据：`docs/evidence/new3_20261025.txt`。
回归脚本：`test_new3_live.py`（闭环验证 SET → ACK → 读回）。

---

## 9. 蓝牙畅连（多点连接）

### 9.1 读 / 写

```
TX  xx 27 01 00 02 80 00          # GET, cmdId=128 (0x80)
RX  xx 27 02 00 03 80 01 <0/1>

TX  xx 01 01 00 03 01 01 <0/1>    # SET, cid=1, cmdId=1, payloadLen=1
RX  xx 01 02 00 03 01 01 00
```

### 9.2 ⚠️ 切换必然导致 BLE 断链（不是 bug，是固件行为）

**这句话必须写清楚：只要下发 `SET_MP`（开或关都一样），耳机一定会在回完 ACK 后的几毫秒内主动断开 BLE。** 实测三次，间隔分别是 3 ms / 46 ms / 16 ms：

| 时刻 | 事件 | 距 ACK |
|---|---|---|
| 15:49:55.645 | `TX SET_MULTIPOINT=1` → `00 01 01 00 03 01 01 01` | — |
| 15:49:55.814 | `RX RESP cid=1 cmd=1 len=1` → `03 01 02 00 03 01 01 00`（成功） | — |
| 15:49:55.817 | 连接断开 | **+3 ms** |
| 15:50:24.419 | `SET_MULTIPOINT=0` ACK 成功 | — |
| 15:50:24.465 | 连接断开 | **+46 ms** |
| 15:51:49.029 | `SET_MULTIPOINT=1` ACK 成功 | — |
| 15:51:49.045 | 连接断开 | **+16 ms** |

日志原文：`docs/evidence/fiil_log_20261025_155218.txt`（383 行）。

**可以排除的两个误判**：
- 不是我们的写入造成的 —— 三条 SET 帧格式完全正确，耳机回的都是成功 ACK（`payload=00`）。
- 不是设置没生效 —— 断开后重连读回，值确实变了（`15:49:26.833` 读「关」→ `15:50:09.679` 读「开」→ `15:50:40.456` 读「关」）。

**机制**：多点连接改变耳机的主从角色（哪台设备是 primary），耳机必须重建 BLE 侧的连接参数，于是直接踢掉当前链路。这与官方 FIIL+ App 的行为一致（其 `case 6:` 收到断开消息后也只是把界面置为断开态）。

**恢复时间**：断开后耳机**不会自己回来**。实测重新可连需要 **8~13 秒**（`15:49:55.817` 断 → `15:50:07.054` 连上 = 11.2 s；`15:50:24.465` 断 → `15:50:37.891` 连上 = 13.4 s）。

**程序必须做到的三件事**（`ble_worker.py` 已实现）：
1. **下发前置标志**：`_handle()` 在 `method == "set_multipoint"` 时先置 `_reconnect_after_multipoint = True`，再发帧。必须在发之前——断线回调可能早于任何事后赋值。
2. **断线回调走「切换中」分支**：不发「连接丢失」红色错误，改发中性提示，并起 `_reconnect_loop()`。
3. **退避重连 + 重做握手**：`delays = (2, 3, 5, 8, 12)` 秒共 5 次；成功后跑 `_connect_inner(keep_state=True)`，里面包含 `setUnlock` 和全部状态重读。**重连后必须重新 `setUnlock`**，否则耳机对所有查询静默（见 §6）。全部失败才提示用户手动介入。

**UI 侧**：`app.py` 的 `conn` 事件对「切换中」保留旧读数不清屏，避免 10 秒的重连窗口里界面全变 `--`；也不在 1.5 s 后发校验 GET（那时必然未连接），校验改为重连成功后由握手重读完成。

---

## 10. EQ

### 10.1 读

```
TX  xx 27 01 00 02 04 00
RX  xx 27 02 00 0E 04 0A 0E 08 06 04 02 00 01 FA FC FE 02
                     │  │  └────── 12 字节曲线（有符号）──────
                     │  └ payloadLen = 0x0C = 12（帧头字段）
                     └ cmdId = 4
```

**payload 共 12 字节，没有内部长度前缀**（`0x0C` 早就作为 payloadLen 在帧头里了）。

曲线值是有符号的：`0A=+10, 0E=+14, 08=+8, 06=+6, 04=+4, 02=+2, 00=0, 01=+1, FA=-6, FC=-4, FE=-2, 02=+2`。范围约 **-6 ~ +14**（对应 ±12 dB 档）。

### 10.2 写（⚠️ 未确认生效）

```
TX  xx 20 01 00 0C <12 字节>      # cid=32 (0x20), cmdId=0
RX  xx 20 02 00 01 00             # ACK 成功
```

但**读回仍是旧值**（`docs/evidence/set4_20261025_003607.txt` 的「写入一致性 不一致 ✗」）。

**结论：EQ 写入的 payload 结构还没搞对，标为 `未确认`，不放进语义化按钮，只保留在原始 Hex 控制台。**

---

## 11. 媒体控制

| 功能 | 帧 | 说明 |
|---|---|---|
| 播放/暂停 | `xx 21 01 00 03 02 01 <0/1>` | cid=33, cmdId=2 |
| 下一曲 | `xx 21 01 00 02 05 00` | cid=33, cmdId=5 |
| 上一曲 | `xx 21 01 00 02 04 00` | cid=33, cmdId=4 |
| 音量+ | `xx 21 01 00 03 01 01 01` | cid=33, cmdId=1 |
| 音量- | `xx 21 01 00 03 01 01 00` | cid=33, cmdId=1 |

实测均有 ACK，但**耳机是否真的执行未验证**（播放状态是否变化需要人耳确认）。

---

## 12. 未确认项清单

| # | 项目 | 现状 | 风险 |
|---|---|---|---|
| 1 | EQ 写入 | ACK 成功但读回不变 | 中——不要放进语义化 UI |
| 2 | ~~低延迟 2/3 档（音乐/视频）~~ | **已定性**：Mate 机型耳机明确拒绝（ACK payload=01，读回不变），不是「未验证」。GUI 只做 0/1 两档，GAIA 机型读到时自动补按钮 | 无（已如实告知用户） |
| 3 | ~~MAF 值 3（兼听）/ 4（风中）~~ | **已确认可用**（`exp_maf_five.py`：SET 0→4 逐档 ACK 成功、@3.0 s 读回一致），已从风险清单移除 | 无 |
| 4 | 佩戴检测（cmdId=9） | 空帧，机型不支持 | 无——直接隐藏 |
| 5 | 媒体控制实际效果 | 有 ACK，未人耳确认 | 低 |
| 6 | `cid=40 cmd=0x83` 的 NOTIFY | 结构未解（`0B 28 03 00 0E 83 0C D8 80 83 8A F0 18 00 C3 0A B3 7F 60`） | 低——可安全忽略 |
| 7 | ~~电量三值在本机的确切归属~~ | **已确认**：F046/F042 为单电池只读 `payload[1]`；F045/F047/F049/F051/F053 为左/右/盒三值（源码逐机型核对 + 20 次采样）。已从风险清单移除 | 无 |
| 8 | PID/左右耳 UUID 的业务含义 | 有回包，语义未解 | 无——只读不控 |
| 9 | 同时连接 SPP 与 BLE GATT | 未测 | 中——手机模块需注意 |
| 10 | 其他机型（非 Key Max）的命令差异 | 未测 | 中——需按机型表适配 |
| 11 | ~~语音提示音量~~ | **已确认**：cid=1 cmd=3，四档（0 高/1 中/2 低/3 静音，线值反序），三档实测 ACK+读回全一致 | 无 |
| 12 | 畅连切换后的重连时序 | 切换后 BLE 立即断链，2~3 s 内可重连；**「重连后是否需要重新 setUnlock」已由 `test_new3_live.py` 证实必须重做** | 低——程序已自动重做握手 |

---

## 13. 给 Android 模块的移植说明

手机模块（`moe.chenxy.fiilpods`）的 `FiilGatt.kt` 与本文件完全对应：

| 本文件 | Android 实现 |
|---|---|
| §1 写 `ff17` / 通知 `ff18` | `FiilGatt.CHAR_WRITE` / `CHAR_NOTIFY` |
| §2 帧格式 | `FiilMate.buildFrame` / `parse` / `drain` |
| §4 setUnlock | `FIILProtocol.unlock()` → `handshake()` 首条 |
| §5 电量 | `FiilGaia.parseBatteryLevel` |
| §6 MAF | `FIILProtocol.setMaf` |
| §7 影院 | `FIILProtocol.set_cinema` 对应 `setCinemaMode` |
| §2.5 seq 不可关联 | **两边都不能用 seq 匹配** |
| §2.2 lenField==1 | **两边的 drain 都必须支持 6 字节短帧** |

**移植时最容易漏的三条**：①setUnlock 握手 ②lenField==1 的 6 字节 SET 应答 ③type 字节必须接受 0x01/0x02/0x03 三种。

---

## 14. 证据文件索引

| 文件 | 内容 |
|---|---|
| `docs/evidence/gatt_20261025_001610.txt` | GATT 服务/特征枚举 |
| `docs/evidence/log_unlock17_20261025_001932.txt` | ff17 写 setUnlock 成功 |
| `docs/evidence/log_unlock16_20261025_001943.txt` | ff16 写 setUnlock **无响应**（干扰项证据） |
| `docs/evidence/table_20261025_002133.txt` | GET 全表首轮 |
| `docs/evidence/unverified_cmdid_20261025_014438.txt` | **18 条 cmdId 全量实机验证** |
| `docs/evidence/set4_20261025_003607.txt` | EQ 写入 / 媒体控制 / 畅连断链 |
| `docs/evidence/maf_timing_20261025_012651.txt` | MAF 生效延迟（粗采样，低估为 1.5 s；精确值见 §6.5） |
| `exp_maf_latency.py` | **精确计时实验**：400 ms 轮询 + NOTIFY 计时，实测 1.75 s / 1.95 s |
| `docs/evidence/set_matrix_20261025_015030.txt` | SET 三命令时序矩阵 |
