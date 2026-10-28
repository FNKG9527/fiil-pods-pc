"""收发日志的纯数据缓冲（不依赖 tkinter）。

v1.2.0 把日志从主界面搬到「设置 → 日志」页，于是日志不能再依赖某个 Text 控件
一直存在：窗口关掉、重开都要能看到完整历史。这里把日志抽成带容量上限的缓冲，
Text 控件退化成它的一个视图，复制/导出直接读缓冲，窗口没开也能用。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 默认保留条数（设置里可改）。超限时丢最旧的，避免长时间挂机把内存吃满。
DEFAULT_CAPACITY = 4000


@dataclass(frozen=True)
class LogEntry:
    """一行日志。tag 决定颜色（tx/rx/err/sys/parse），direction 只有 TX/RX 才有。"""

    clock: str
    tag: str
    text: str
    direction: str = ""

    def line(self) -> str:
        """和界面上看到的完全一致的文本（复制/导出 TXT 用它）。"""
        return f"{self.clock}  {self.text}" if self.clock else self.text


class LogStore:
    """有容量上限的日志缓冲。"""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._entries: list[LogEntry] = []
        self._capacity = max(1, int(capacity))
        self._dropped = 0

    # -- 读 ---------------------------------------------------------------
    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def dropped(self) -> int:
        """因为超出容量而被丢弃的条数（界面上提示「仅显示最近 N 条」用）。"""
        return self._dropped

    def entries(self) -> tuple[LogEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def as_text(self) -> str:
        return "".join(entry.line() + "\n" for entry in self._entries)

    def csv_rows(self) -> list[dict[str, str]]:
        """只有收发帧进 CSV；界面提示类日志（sys/err/parse）不混进数据表。"""
        return [
            {"time": e.clock, "dir": e.direction, "data": e.text, "note": ""}
            for e in self._entries
            if e.direction
        ]

    # -- 写 ---------------------------------------------------------------
    def add(self, tag: str, clock: str, text: str, direction: str = "") -> LogEntry:
        entry = LogEntry(clock=clock, tag=tag, text=text, direction=direction)
        self._entries.append(entry)
        overflow = len(self._entries) - self._capacity
        if overflow > 0:
            del self._entries[:overflow]
            self._dropped += overflow
        return entry

    def set_capacity(self, capacity: int) -> int:
        """改容量（设置里调整保留条数时调用）。返回本次因缩容丢掉的条数。"""
        self._capacity = max(1, int(capacity))
        overflow = len(self._entries) - self._capacity
        if overflow > 0:
            del self._entries[:overflow]
            self._dropped += overflow
            return overflow
        return 0

    def clear(self) -> None:
        self._entries.clear()
        self._dropped = 0
