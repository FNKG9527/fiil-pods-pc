"""统一的界面主题：配色、字体、卡片样式（不 import tkinter 的部分放在这里）。

为什么单独一个模块：v1.2.0 之前颜色是散落在 app.py 各处的字符串字面量
（"#1a5fb4"、"#c0392b"…），同一含义在不同控件上用了不同色号，改一次要翻半天。
这里把「语义色」定死，界面只引用语义名，不再写字面量。
"""

from __future__ import annotations

#: 语义色板。key 是用途，value 是十六进制颜色。
PALETTE: dict[str, str] = {
    "bg": "#f5f6f8",          # 窗口底色
    "card": "#ffffff",        # 卡片底色
    "border": "#dfe3e8",      # 卡片描边
    "text": "#1f2328",        # 主文字
    "muted": "#6b7280",       # 次要说明文字
    "accent": "#1a5fb4",      # 强调（当前值、选中态）
    "ok": "#2c7a2c",          # 已连接 / 已生效
    "warn": "#8a5a00",        # 切换中 / 需要用户注意
    "error": "#c0392b",       # 错误 / 未连接
    "idle": "#9aa0a6",        # 未知 / 未读取
    "chip_bg": "#eef2f7",     # 状态胶囊底色
}

#: 字体族与字号。等宽字体用于日志与 Hex。
FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Consolas"
FONT_SIZE_BASE = 10
FONT_SIZE_TITLE = 12
FONT_SIZE_BIG = 20

#: 日志标签 → 颜色 key。和 app 里 Text 控件的 tag 名一一对应。
LOG_TAG_COLORS: dict[str, str] = {
    "tx": "accent",
    "rx": "ok",
    "err": "error",
    "sys": "warn",
    "parse": "text",
}


def color(name: str) -> str:
    """按语义名取色；未知名字直接报错，避免拼错时静默变黑字。"""
    try:
        return PALETTE[name]
    except KeyError as exc:
        raise KeyError(f"未定义的界面颜色 {name!r}，可用：{sorted(PALETTE)}") from exc


def tone_color(tone: str) -> str:
    """总览卡片的 tone（ok/warn/idle/off）→ 颜色。off 用 muted，不抢眼。"""
    if tone == "off":
        return PALETTE["muted"]
    if tone in PALETTE:
        return PALETTE[tone]
    return PALETTE["text"]
