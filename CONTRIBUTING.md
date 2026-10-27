# 贡献指南

## 硬规则：没有来源的结论不合并

本仓库的每一条协议结论都必须可追溯。**"我试了一下感觉是这样"不算来源。**

来源只有两种，二选一：

### 来源 A：反编译产物的 `文件:行号`

```
com/fengeek/main/f046/fragment/F046HomeFragment.java:623
```

引用时给到**能定位到具体方法**的粒度，并附上关键代码行。
`jadx_out/` 反编译产物本身**不要提交**（体积大且有版权问题）。

### 来源 B：`docs/evidence/` 下的原始日志

抓包日志必须是**未经编辑的原始 TXT/RX 十六进制**，且文件头包含四项：

| 字段 | 示例 |
|---|---|
| 设备型号 | `FIIL Key Max` |
| 固件版本 | `3.0.1.1` |
| 采集时间 | `2026-10-25 00:16 — 01:50` |
| 采集方式 | `app.py ⑧ 静默监听` / `probe_gatt.py` |

日志格式约定见 [docs/evidence/README.md](docs/evidence/README.md)。

**提交前必须脱敏**：

- 蓝牙地址一律换成文档里的占位值（`B0:F1:A3:00:00:01` 这类）
- 本机绝对路径换成相对路径
- 去掉任何能定位到个人的信息

---

## 提交什么

| 类型 | 是否接受 | 说明 |
|---|---|---|
| 新机型实测数据 | ✅ 欢迎 | 请附完整证据 + 机型/固件版本 |
| 协议勘误 | ✅ 欢迎 | 必须指出原文档哪一条错了，并给出来源 |
| 新功能 / GUI 改进 | ✅ 欢迎 | 先开 issue 讨论，避免白写 |
| 无来源的"经验之谈" | ❌ 不接受 | 见上面的硬规则 |
| 厂商 APK / 反编译产物 | ❌ 不接受 | 版权问题 |
| 真实设备 MAC / 个人日志 | ❌ 不接受 | 隐私问题 |
| 打包好的 exe | ❌ 不接受 | 走 GitHub Releases，不进版本库 |

---

## 提交方式

1. Fork 本仓库
2. 新建分支：`git checkout -b fix/maf-latency` 或 `feat/multipoint-toggle`
3. 改动并自测：
   ```bash
   pytest tests -q
   ruff check .
   ```
4. 提交，commit message 用 `<type>: <描述>`：

   | type | 用途 |
   |---|---|
   | `feat` | 新功能 |
   | `fix` | 修 bug |
   | `docs` | 只改文档 |
   | `test` | 只改测试 |
   | `refactor` | 重构，不改行为 |
   | `build` | 打包脚本 / 依赖 |
   | `chore` | 杂项 |

   示例：`fix: 修正 F046 电量帧 payload[1] 的越界判断`

5. 开 Pull Request，**在描述里写清楚来源**（文件:行号 或 日志文件名）

---

## 代码规范

- **Python 3.10+**，`from __future__ import annotations` 可省
- 行宽 110，双引号，LF 换行（见 `ruff.toml`）
- 注释写**为什么**，不写**是什么**——尤其是"这里为什么不能用更简单的写法"
- 协议相关的新常量，必须在注释里标明来源
- GUI 代码用 tkinter，不引入 Qt / wxPython 等重型依赖
- 新增第三方依赖前先在 issue 里讨论

### 测试要求

- 协议层改动必须加**离线测试**（`tests/`，不连设备也能跑）
- 需要真机的验证脚本放 `tools/`，并在文件头写清楚"会连接真实设备"
- 测试文件要同时满足两种跑法：
  ```bash
  pytest tests -q                  # pytest 收集
  python tests/test_protocol.py    # 独立自检脚本
  ```

---

## 不要提交

```
dist/                 # 构建产物
out/                  # 本机实测日志与截图
known_devices.json    # 含真实蓝牙地址
*.exe  *.msi          # 二进制
jadx_out/             # 反编译产物
.env  venv/           # 本地环境
```

`.gitignore` 已覆盖以上路径。发布版走 [Releases](https://github.com/FNKG9527/fiil-pods-pc/releases)。

---

## 审查流程

1. 维护者检查**来源是否成立**（这是第一道，也是最重要的一道）
2. 检查是否引入敏感信息（MAC、路径、日志）
3. 跑 CI：`ruff` + `pytest`（Windows / Ubuntu / macOS × py3.10 / 3.12）
4. 合并

来源不成立的 PR 会被打回，**补充来源后可以重开**。

---

## 行为准则

参与本项目即表示同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
