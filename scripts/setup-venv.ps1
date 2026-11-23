# 按 requirements.txt 安装 / 更新仓库内 .venv 的依赖。uv 缓存和临时文件都放在仓库内的 .cache\（不占系统盘）。
# 用法:  powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1
$ErrorActionPreference = 'Stop'
$Hub = Split-Path -Parent $PSScriptRoot
$Cache = Join-Path $Hub '.cache'
New-Item -ItemType Directory -Force (Join-Path $Cache 'uv'), (Join-Path $Cache 'tmp') | Out-Null

# 只对本进程生效
$env:UV_CACHE_DIR = Join-Path $Cache 'uv'
$env:TMP = Join-Path $Cache 'tmp'
$env:TEMP = $env:TMP

$py = Join-Path $Hub '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    # 首次安装：用 uv 建 .venv（Python 版本见 .python-version，没有就用 uv 默认）
    Write-Host "==> 创建 $Hub\.venv"
    uv venv (Join-Path $Hub '.venv')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $py)) { throw "uv venv 失败（exit $LASTEXITCODE）" }
}

Write-Host "==> 安装依赖到 $Hub\.venv（缓存: $env:UV_CACHE_DIR）"
uv pip install --python $py -r (Join-Path $Hub 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw "uv pip install 失败（exit $LASTEXITCODE）" }

# uv 生成的 .venv\Scripts\pythonw.exe 是控制台程序（与 python.exe 相同），会启动带黑窗口的 python.exe 并抢焦点。
# 换成 CPython 自带的 GUI 版 venv 启动器：它读 pyvenv.cfg 的 home，启动无窗口的 pythonw.exe。
function Get-PeSubsystem($Path) {
    $b = [IO.File]::ReadAllBytes($Path); $pe = [BitConverter]::ToInt32($b, 0x3c)
    return [BitConverter]::ToUInt16($b, $pe + 0x5c)   # 2 = GUI, 3 = 控制台
}
$pyw = Join-Path $Hub '.venv\Scripts\pythonw.exe'
if ((Get-PeSubsystem $pyw) -ne 2) {
    $home_ = ((Get-Content (Join-Path $Hub '.venv\pyvenv.cfg')) -match '^home\s*=' -replace '^home\s*=\s*', '')
    $gui = Join-Path $home_ 'Lib\venv\scripts\nt\pythonw.exe'
    if (-not (Test-Path $gui) -or (Get-PeSubsystem $gui) -ne 2) { throw "找不到 GUI 版 venv 启动器：$gui" }
    $bak = Join-Path $Cache 'pythonw.console-trampoline.bak'
    Remove-Item $bak -ErrorAction SilentlyContinue
    Move-Item $pyw $bak          # 运行中的 exe 不能覆盖，但可以改名
    Copy-Item $gui $pyw
    Write-Host "==> 已把 .venv\Scripts\pythonw.exe 换成无窗口版本"
}

Write-Host "`n==> 自检"
& $py (Join-Path $Hub 'scripts\check_env.py')
Write-Host "`n完成。"
