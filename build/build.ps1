# 一键重新打包（PowerShell）
# 用法：  powershell -ExecutionPolicy Bypass -File build\build.ps1
# 前置：  pip install pyinstaller  +  安装 Inno Setup 6
#
# 注意：本文件必须保存为「UTF-8 with BOM」。
# powershell.exe 5.1 读取无 BOM 的 .ps1 时会按系统 ANSI 代码页(GBK)解码，
# 中文注释/字符串被打乱后会吞掉引号，导致命令被当成字符串 literal 而不执行，
# 且脚本仍以 exit 0 正常结束（v1.3.1 曾因此打出旧版 exe）。编码校验见文末。
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 版本号单一来源：app.py 的 APP_VERSION，避免脚本、iss、version_info 三处走样
$appPy = Join-Path $root 'app.py'
$appVersion = (Select-String -Path $appPy -Pattern '^APP_VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
if (-not $appVersion) { throw 'app.py 里没找到 APP_VERSION' }
$dottedVersion = "$appVersion.0"
Write-Host "目标版本：$appVersion" -ForegroundColor Cyan

$py = 'python'
if ($env:PYTHON) { $py = $env:PYTHON }

Write-Host '[1/3] PyInstaller 构建目录版 exe ...' -ForegroundColor Cyan
& $py -m PyInstaller --clean --noconfirm build\fiil_pods.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 构建失败' }

Write-Host '[2/3] Inno Setup 编译安装包 ...' -ForegroundColor Cyan
$iscc = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
if (-not (Test-Path $iscc)) { $iscc = 'C:\Program Files\Inno Setup 6\ISCC.exe' }
if (-not (Test-Path $iscc)) { throw '没找到 Inno Setup 6，请先安装 https://jrsoftware.org/isdl.php' }
& $iscc 'build\installer.iss'
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup 编译失败' }

# 产物版本校验：把"静默打出旧 exe"这类错误挡在发布之前
$exePath = Join-Path $root 'dist\FiilPodsPC\FiilPodsPC.exe'
$setupPath = Join-Path $root "dist\installer\FiilPodsPC_Setup_$appVersion.exe"
foreach ($item in @($exePath, $setupPath)) {
    if (-not (Test-Path $item)) { throw "缺少产物：$item" }
    $actual = (Get-Item $item).VersionInfo.FileVersion
    # Inno Setup 写出的 FileVersion 常带尾随空格，比较前先去掉
    if ($actual) { $actual = $actual.Trim() }
    if ($actual -ne $dottedVersion) {
        throw "$item 的版本资源是 '$actual'，期望 $dottedVersion"
    }
}

Write-Host '[3/3] 完成' -ForegroundColor Green
Write-Host "  安装包：$setupPath"
Write-Host "  目录版：$exePath"
