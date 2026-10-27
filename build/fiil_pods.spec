# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：FiilPods PC 探针（tkinter + bleak/WinRT）。

构建： python -m PyInstaller --clean --noconfirm build\\fiil_pods.spec
产物： dist\\FiilPodsPC\\FiilPodsPC.exe（目录模式，Inno Setup 再打成安装包）
"""
import os
import shutil

from PyInstaller.utils.hooks import collect_submodules

# 项目根目录：spec 在 build/ 下，SPECPATH 由 PyInstaller 注入
PROJ = os.path.dirname(os.path.abspath(SPECPATH))

hiddenimports = []
# WinRT 是命名空间包，PyInstaller 静态分析看不到 bleak 运行时动态 import 的模块
for pkg in ("winrt", "bleak"):
    hiddenimports += collect_submodules(pkg)

# 设备表：仓库里只有脱敏样例（known_devices.json 本身在 .gitignore 里，
# 含本机真实地址）。打包时用样例顶替，安装版首次运行时由
# ble_worker._seed_known_devices() 复制到 %LOCALAPPDATA%\FiilPodsPC。
_seed_src = os.path.join(PROJ, "known_devices.example.json")
_seed_dst = os.path.join(PROJ, "build", "known_devices.json")
if os.path.exists(_seed_src):
    shutil.copyfile(_seed_src, _seed_dst)

a = Analysis(
    [os.path.join(PROJ, "app.py")],
    pathex=[PROJ],
    binaries=[],
    datas=[
        (os.path.join(PROJ, "build", "app.ico"), "."),
        (_seed_dst, "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["numpy", "PIL", "pytest", "unittest", "pydoc_data", "test"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FiilPodsPC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI 程序：不弹黑色控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(PROJ, "build", "app.ico"),
    version=os.path.join(PROJ, "build", "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FiilPodsPC",
)
