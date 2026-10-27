"""启动 GUI 并截图，验证三个新区块确实渲染出来（无设备连接也能看布局）。"""

# --- 仓库内脚本引导：让 tools/ 下的脚本能 import 根目录的核心模块 ---
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import tkinter as tk

from PIL import ImageGrab

import app as A

root = tk.Tk()
a = A.App(root)
root.update_idletasks()
root.update()
root.deiconify()
root.lift()
root.attributes("-topmost", True)
root.update()
root.after(400)
root.update()

x, y = root.winfo_rootx(), root.winfo_rooty()
w, h = root.winfo_width(), root.winfo_height()
print("geometry", x, y, w, h)
img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
out = r"out/gui_three_blocks.png"
img.save(out)
print("saved", out, img.size)

# 断言三个新区块的控件都建出来了
assert a.ll_var is not None and len(a.ll_btns) == 2
assert a.mp_on is not None and a.mp_state is not None
assert a.tone_var is not None and len(a.tone_btns) == 4
print("WIDGETS OK: ll_btns=2, tone_btns=4")

root.destroy()
