# 更新日志

本文件格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [1.0.0] - 2026-10-27

首个公开版本。

### 新增

**应用**

- `app.py` — tkinter GUI 控制台
  - ① 设备扫描 / 直连地址 / 断开，记住上次连过的设备
  - ② 电量（按机型分流解析，含短帧兜底）
  - ③ 降噪模式 MAF（五档读写 + 写后读回确认）
  - ④ 影院模式（空间音频）读写
  - ⑤ 低延迟模式读写（机型分流：Mate 机型只有 0/1）
  - ⑥ 蓝牙畅连（多点连接）与语音提示音量
  - ⑦ 原始 Hex 控制台（手写字节直发）
  - ⑧ 静默监听（只收不发，用于逆向取证）
  - 右侧「收发日志（原始字节）」/「解析结果」双页签，支持导出 CSV
- `ble_worker.py` — BLE 异步工作线程 + 事件队列，`setUnlock` 自动握手，断连自动重做
- `fiil_protocol.py` — 协议层：组帧、流式重组、解析、枚举

**打包**

- `build/fiil_pods.spec` — PyInstaller 目录版构建
- `build/installer.iss` — Inno Setup 安装包（简体中文 / English 双语，默认简中）
  - 默认装到当前用户目录，不需要管理员权限
  - 开始菜单快捷方式 + 可选桌面图标
  - 卸载时可选一并清除 `%LOCALAPPDATA%\FiilPodsPC`
- `build/build.ps1` — 一键重建
- `build/app.ico` — 程序图标

**文档**

- `docs/PROTOCOL_PC.md` — 协议规格（710 行，含 Service/Characteristic 表、帧格式、18 条 GET 全表、连接初始化流程、各功能读写与生效延迟）
- `docs/evidence/` — 15 份已脱敏的原始抓包日志 + 索引
- `README.md` — 中文主文档

**仓库**

- GPL-3.0 代码许可 + CC BY-SA 4.0 文档许可
- `NOTICE` / `DISCLAIMER.md` / `CONTRIBUTING.md` / `SECURITY.md` / `CODE_OF_CONDUCT.md`
- GitHub Actions CI：`ruff` + `pytest`（Windows / Ubuntu / macOS × py3.10 / 3.12）

**测试**

- `tests/` 5 个离线测试文件，共 140 项断言，不需要耳机
- `tools/` 探针 / 实验 / 真机验证脚本

### 已知限制

- 只在 **Windows 10/11** 上做过实机验证；Linux / macOS 理论可用但未测试
- 实测机型仅 **FIIL Key Max（固件 3.0.1.1）** 与 **FIIL Atom**
- EQ 写入**未确认生效**，只保留在原始 Hex 控制台
- 低延迟档位是**分机型**的（Mate 机型只接受 0/1，SET 2/3 被耳机 ACK 拒绝）
- `cmdId=5` 曾被误认为 earType，已证伪，见协议文档 §3.1
- 安装包**未做代码签名**

### 逆向依据

FIIL+ 3.5.2（`com.fengeek.f002`），jadx 静态分析 + 实机 BLE 抓包。
实测环境：Windows 11 + Python 3.12.6 + bleak（WinRT 后端），
采集时间 2026-10-25 00:16 — 01:50。

---

## 版本号约定

- **主版本**：协议描述有破坏性更正（例如某条帧格式被证明是错的），或 GUI 大改
- **次版本**：新增机型、新增功能、新增文档章节
- **修订号**：勘误、排版、错别字、打包脚本修复

---

[1.0.0]: https://github.com/FNKG9527/fiil-pods-pc/releases/tag/v1.0.0
