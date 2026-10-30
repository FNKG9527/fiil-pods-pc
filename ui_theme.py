"""统一的界面主题：配色、字体、卡片样式（不 import tkinter 的部分放在这里）。

为什么单独一个模块：v1.2.0 之前颜色是散落在 app.py 各处的字符串字面量
（"#1a5fb4"、"#c0392b"…），同一含义在不同控件上用了不同色号，改一次要翻半天。
这里把「语义色」定死，界面只引用语义名，不再写字面量。

v1.4.0 重做：原来的色板全是 `#f5f6f8` / `#ffffff` / `#dfe3e8` 这类「白底浅灰边」的
中性色，界面看起来很素；字号又给了 18/20 号（电量、总览卡片），加上每格
上下各 10px 内边距，一个卡片 82px 高、左栏面板堆到 1223px —— 窗口只有 682px，
用户必须滚很久。这一版把「素」和「大」一起解决：

- 颜色补上品牌蓝主色 + 三级层次（bg / surface / surface_alt / border），
  强调色从灰蓝 #1a5fb4 换成更饱和的 #2563eb，状态色也给足对比度。
- 字号阶梯收窄：正文 9、标题 10、数值 13（原来是 18），巨大的数字不再喧宾夺主。
- 新增 SPACE 间距常量，内边距统一走它，避免各处手写 6/8/10 各说各话。
"""

from __future__ import annotations

#: 语义色板。key 是用途，value 是十六进制颜色。
PALETTE: dict[str, str] = {
    # -- 层次 --
    "bg": "#eef1f6",          # 窗口底色（比卡片深，卡片才浮得起来）
    "surface": "#ffffff",     # 内容承载面（输入框、列表）
    "card": "#ffffff",        # 卡片底色
    "card_alt": "#f7f9fc",    # 次级卡片 / 隔行底色
    "border": "#d5dbe4",      # 描边
    "border_soft": "#e6eaf1",  # 更淡的分隔线
    # -- 文字 --
    "text": "#141a23",        # 主文字
    "muted": "#5c6675",       # 次要说明文字
    "faint": "#8b95a5",       # 极弱提示（帧结构说明这类）
    # -- 品牌与状态 --
    "accent": "#2563eb",      # 强调（当前值、选中态）
    "accent_hover": "#1d4ed8",
    "accent_soft": "#e7efff",  # 强调色淡底（选中胶囊、当前档）
    "ok": "#0f9d58",          # 已连接 / 已生效
    "ok_soft": "#e4f7ec",
    "warn": "#b45309",        # 切换中 / 需要用户注意
    "warn_soft": "#fdf1de",
    "error": "#d92d20",       # 错误 / 未连接
    "error_soft": "#fde8e6",
    "idle": "#98a2b3",        # 未知 / 未读取
    "chip_bg": "#e7efff",     # 状态胶囊底色
    "header_bg": "#16233a",   # 顶部品牌条底色（深色，压住整体）
    "header_text": "#f2f6fc",
    "header_muted": "#9fb0c9",
}

#: 字体族与字号。等宽字体用于日志与 Hex。
FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Consolas"
#: 正文基准字号（原来 10，偏大；9 在 96DPI 下紧凑且清晰）
FONT_SIZE_BASE = 9
#: 区块标题（原来 12）
FONT_SIZE_TITLE = 10
#: 面板里的大数值：电量、总览卡片（原来 18/20，是「太大」的主因）
FONT_SIZE_VALUE = 13
#: 顶部品牌条标题
FONT_SIZE_BRAND = 12
#: 小字说明（面板脚注、帧结构解释）
FONT_SIZE_SMALL = 8

#: 统一间距阶梯（像素）。所有 padx/pady 从这里取，别再手写 6/8/10。
SPACE = {
    "xs": 2,
    "sm": 4,
    "md": 6,
    "lg": 10,
    "xl": 14,
}

#: 面板之间的竖向间隔（原来每个面板 pady=(0,6)，且内部还有 8px 内边距）
#: 4 而不是 5：默认 1120x720 下左栏比视口高 8px，五张卡各收 1px 即可消除滚动条。
PANEL_GAP = 4
#: 面板内部左右内边距（原来 8）
PANEL_PADX = 8
#: 面板内部上下内边距（原来 6~8）：收到 4，与 PANEL_GAP 一起把左栏塞进默认窗口
PANEL_PADY = 4
#: 总览卡片的内部左右内边距（原来 12）
CARD_PADX = 9
#: 总览卡片 label 上内边距 / value 下内边距（原来 10/10）
CARD_PADY = 5

#: 左侧栏宽度（原来 470，收窄到 430 给右侧总览更多空间）
LEFT_PANE_WIDTH = 430

#: 日志标签 → 颜色 key。和 app 里 Text 控件的 tag 名一一对应。
LOG_TAG_COLORS: dict[str, str] = {
    "tx": "accent",
    "rx": "ok",
    "err": "error",
    "sys": "warn",
    "parse": "text",
}

#: tone → (前景色 key, 淡底 key)。总览卡片用淡底渲染左侧色条与数值底色。
TONE_SOFT: dict[str, str] = {
    "ok": "ok_soft",
    "warn": "warn_soft",
    "error": "error_soft",
    "off": "card_alt",
    "idle": "card_alt",
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


def tone_soft(tone: str) -> str:
    """tone → 淡底颜色。未知 tone 退到 card_alt，不会抛异常。"""
    return PALETTE[TONE_SOFT.get(tone, "card_alt")]


def ui_font(size: int | None = None, weight: str = "normal") -> tuple[str, int]:
    """界面字体元组（Tk 只接受 (family, size[, style]) 形式）。"""
    return (FONT_UI, size if size is not None else FONT_SIZE_BASE)


def value_font() -> tuple[str, int, str]:
    """面板/卡片里的大数值。"""
    return (FONT_UI, FONT_SIZE_VALUE, "bold")


def mono_font(size: int = FONT_SIZE_BASE) -> tuple[str, int]:
    return (FONT_MONO, size)
