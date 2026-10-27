# FIIL 耳机 PC 控制台（FiilPods PC）

> **一个不用手机、不装官方 App，直接在 Windows 上用蓝牙控制 FIIL 耳机的桌面工具。**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](#平台支持)
[![Protocol](https://img.shields.io/badge/transport-BLE%20GATT-orange.svg)](docs/PROTOCOL_PC.md)

---

## 这是什么

FIIL 耳机的降噪、影院模式、低延迟、多点连接等功能，官方只在手机 App（FIIL+）里提供。
本项目把这套 **BLE GATT 协议**完整逆向出来，做成一个 Windows 桌面控制台：

- **能用的功能全部有按钮**：电量、降噪五档、影院模式、低延迟、蓝牙畅连、语音提示音量
- **没验证的功能不乱做**：只放在「原始 Hex 控制台」里，手写字节自己试
- **每一帧收发都看得见**：右侧实时显示十六进制原始字节 + 解析结果，可直接导出 CSV
- **结论都有证据**：协议文档里每条规则都标注了实测日志或反编译 `文件:行号`

> 本项目是**协议调试探针**，不是官方客户端的替代品。
> 它不绕过任何加密或认证，只把耳机本来就支持、但官方只在手机端开放的功能搬到 PC。
> 详见 [DISCLAIMER.md](DISCLAIMER.md)。

---

## 功能

| # | 功能 | 读 | 写 | 实测状态 |
|---|---|---|---|---|
| ① | 设备扫描 / 直连 / 断开 | — | — | ✅ 已实机验证 |
| ② | 电量（按机型分流解析） | ✅ | — | ✅ 已实机验证 |
| ③ | 降噪模式 MAF（五档） | ✅ | ✅ | ✅ 五态全部实机验证 |
| ④ | 影院模式（空间音频） | ✅ | ✅ | ✅ 已实机验证 |
| ⑤ | 低延迟模式 | ✅ | ✅ | ✅ 已实机验证（机型分流） |
| ⑥ | 蓝牙畅连（多点连接） | ✅ | ✅ | ✅ 已实机验证（切换会断链） |
| ⑥ | 语音提示音量 | ✅ | ✅ | ✅ 已实机验证（线值反序） |
| ⑦ | 原始 Hex 控制台 | — | ✅ | ✅ 任意字节 |
| ⑧ | 静默监听（逆向取证） | ✅ | — | ✅ 只收不发 |
| — | EQ | ✅ | ⚠️ | ⚠️ 写入未确认生效 |

协议细节见 [docs/PROTOCOL_PC.md](docs/PROTOCOL_PC.md)。

---

## 下载安装

### 方式一：安装包（推荐）

从 [Releases](https://github.com/FNKG9527/fiil-pods-pc/releases/latest) 下载 `FiilPodsPC_Setup_x.y.z.exe`，
双击安装。默认装到当前用户目录，**不需要管理员权限**。

安装包会创建开始菜单快捷方式，可选创建桌面图标，卸载时可选一并清除 `%LOCALAPPDATA%\FiilPodsPC` 下的日志与设备表。

> 安装包未做代码签名，Windows SmartScreen 可能提示「未知发布者」——
> 点「更多信息」→「仍要运行」即可。源码完全公开，可自行构建。

### 方式二：从源码运行

```bash
git clone https://github.com/FNKG9527/fiil-pods-pc.git
cd fiil-pods-pc
pip install -r requirements.txt
python app.py
```

要求 **Python 3.10+** 与 **Windows 10/11**（依赖 WinRT 蓝牙后端）。

### 方式三：自己打包

```powershell
pip install pyinstaller
# 再装 Inno Setup 6：https://jrsoftware.org/isdl.php
powershell -ExecutionPolicy Bypass -File build\build.ps1
```

产物：`dist\FiilPodsPC\`（目录版）、`dist\installer\FiilPodsPC_Setup_1.0.0.exe`（安装包）。

---

## 快速开始

1. 打开耳机，**确认它没有同时连着手机**（双模耳机被手机占用时可能不出现在扫描列表）。
2. 启动程序，点「扫描设备」。
3. 在列表里选中你的耳机 → 点「连接」。
   - 列表里没有？在「直连地址」里填 BLE 地址（形如 `B0:F1:A3:00:00:01`）后点「按地址连接」。
   - 上次连过的地址会自动记住。
4. 连接成功后左侧各功能块即可使用；右侧会实时显示收发的十六进制帧。

### 数据存放位置

| 内容 | 源码运行 | 安装版 |
|---|---|---|
| 抓包日志 / CSV | `<仓库根>/out/` | `%LOCALAPPDATA%\FiilPodsPC\out\` |
| 已知设备表 | `<仓库根>/known_devices.json` | `%LOCALAPPDATA%\FiilPodsPC\known_devices.json` |

---

## 核心结论

逆向过程中确认的、最容易踩错的几条（完整版见 [docs/PROTOCOL_PC.md](docs/PROTOCOL_PC.md)）：

| 项目 | 结论 |
|---|---|
| 传输层 | **BLE GATT**，服务 `0000fdb3`，写 `ff17`，通知 `ff18` |
| 连接后必须做的事 | **先发 `setUnlock`**，否则耳机对所有命令静默 |
| 帧格式 | `seq cid type 00 [lenField] [cmdId] [payloadLen] payload` |
| `seq` | **不能用来匹配请求/响应**（耳机用自己的滚动计数器） |
| `type` | 必须同时接受 `0x01/0x02/0x03`，只认 `0x01` 会丢掉全部回包 |
| SET 生效延迟 | MAF 约 **1.75~2.0 s**；影院 **0.5 s**；低延迟 **0.45 s** |
| 状态回读 | 以 `cid=40`（NOTIFY）为准，不能依赖 SET 的 ACK |
| SET 成功判据 | 6 字节短帧 ACK `xx cc 02 00 01 <00 成功 / 01 拒绝>`，必须检查 |
| 语音提示音量 | **线值反序**：0 高 / 1 中 / 2 低 / 3 静音 |
| 蓝牙畅连 | **切换后 BLE 立即断链**，需重连并重做 `setUnlock`（固件行为，不是 bug） |

---

## 仓库结构

```
fiil-pods-pc/
├── app.py                  # tkinter GUI 主程序（入口）
├── ble_worker.py           # BLE 异步工作线程 + 事件队列
├── fiil_protocol.py        # 协议层：组帧、解析、枚举
├── tests/                  # 离线测试，不需要耳机
├── tools/                  # 探针 / 实验 / 真机验证脚本
├── docs/
│   ├── PROTOCOL_PC.md      # 协议规格（710 行）
│   └── evidence/           # 已脱敏的原始抓包日志
├── build/                  # PyInstaller spec + Inno Setup 脚本 + 图标
├── .github/workflows/      # CI
├── requirements.txt
├── LICENSE                 # GPL-3.0
├── NOTICE                  # 商标与归属声明
├── DISCLAIMER.md           # 使用边界与合规声明
├── CONTRIBUTING.md
├── SECURITY.md
└── CHANGELOG.md
```

---

## 开发与测试

```bash
# 离线测试（不需要耳机）
pytest tests -q
# 5 passed

# 单独跑某个测试（也是自检脚本）
python -X utf8 tests/test_protocol.py

# 代码风格
ruff check .
```

`tests/` 下的 5 个文件共 **140 项断言**，覆盖组帧、流式重组、电量解析、
设备名回退匹配、ACK 判定、多点连接状态机。

`tools/` 下是需要真机的探针与实验脚本，会实际连接耳机，**运行前请先读脚本开头的说明**。

---

## 平台支持

| 平台 | 状态 |
|---|---|
| Windows 10 / 11 | ✅ 支持（WinRT 蓝牙后端，唯一实机验证过的平台） |
| Linux | ⚠️ 理论可用（bleak 支持 BlueZ），未测试 |
| macOS | ⚠️ 理论可用（bleak 支持 CoreBluetooth），未测试 |

GUI 用 tkinter，三者都能跑。但有几处只在 Windows 上生效的路径，其他平台会静默跳过：

- `app.py:789` 的 `ctypes.windll.shcore.SetProcessDpiAwareness(1)` 包在 `try/except` 里
- `ble_worker.py:157` 用 `Get-PnpDevice` 查已配对设备名，失败时回退到广播名，不影响扫描

也就是说非 Windows 上**核心 BLE 功能可以跑，但已配对设备名兜底和高分屏 DPI 适配没有**。

---

## 常见问题

**Q：扫描列表里没有我的耳机？**
双模耳机被手机占用时不发 BLE 可连接广播。断开手机连接，或在「直连地址」里直接填地址连。

**Q：点了按钮但耳机没反应？**
先看右侧日志里 SET 的 ACK 是不是 `<00>`。`<01>` 表示耳机明确拒绝（多半是机型不支持该档位）。
MAF 有 1.75~2.0 秒延迟，影院 0.5 秒，低延迟 0.45 秒——**不要连点**。

**Q：切换蓝牙畅连后程序报断连？**
这是固件行为，切换必然导致 BLE 断链。重新连接即可，程序会自动重做 `setUnlock`。

**Q：支持哪些型号？**
实测过 FIIL Key Max（固件 3.0.1.1）与 FIIL Atom。同 OUI（`B0:F1:A3` / `FF:F1:A3`）的
其他型号大概率可用，但**电量解析和低延迟档位是分机型的**，其他型号请以实测为准。

**Q：安装包报病毒 / SmartScreen 拦截？**
PyInstaller 打包的 exe 常见误报，安装包也未签名。源码完全公开，可自行 `build\build.ps1` 构建。

---

## 贡献

欢迎提交**带证据的**勘误与新机型实测数据。硬性规则：**没有来源的结论不合并。**

来源只有两种：反编译产物的 `文件:行号`，或 `docs/evidence/` 下的原始 TXT/RX 日志
（需含型号、固件版本、采集时间、采集方式）。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 安全

发现安全问题请**不要开公开 issue**，按 [SECURITY.md](SECURITY.md) 的流程私下报告。

## 许可

代码以 **GNU General Public License v3.0** 发布，见 [LICENSE](LICENSE)。
文档（`docs/`、`README.md`）以 **CC BY-SA 4.0** 发布。

商标归属见 [NOTICE](NOTICE)。使用边界见 [DISCLAIMER.md](DISCLAIMER.md)。

## 致谢

- [bleak](https://github.com/hbldh/bleak) — 跨平台 BLE 客户端
- [PyInstaller](https://pyinstaller.org/) — 打包
- [Inno Setup](https://jrsoftware.org/isinfo.php) — 安装程序
- [jadx](https://github.com/skylot/jadx) — 反编译
