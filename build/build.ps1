# 一键重新打包（PowerShell）
# 用法：  powershell -ExecutionPolicy Bypass -File build\build.ps1
# 前置：  pip install pyinstaller  +  安装 Inno Setup 6
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = 'python'
if ($env:PYTHON) { $py = $env:PYTHON }

Write-Host '[1/3] PyInstaller 构建目录版 exe …' -ForegroundColor Cyan
& $py -m PyInstaller --clean --noconfirm build\fiil_pods.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 构建失败' }

Write-Host '[2/3] Inno Setup 编译安装包 …' -ForegroundColor Cyan
$iscc = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
if (-not (Test-Path $iscc)) { $iscc = 'C:\Program Files\Inno Setup 6\ISCC.exe' }
if (-not (Test-Path $iscc)) { throw '没找到 Inno Setup 6，请先安装 https://jrsoftware.org/isdl.php' }
& $iscc 'build\installer.iss'
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup 编译失败' }

Write-Host '[3/3] 完成' -ForegroundColor Green
Write-Host "  安装包：$root\dist\installer\FiilPodsPC_Setup_1.2.0.exe"
Write-Host "  目录版：$root\dist\FiilPodsPC\FiilPodsPC.exe"
