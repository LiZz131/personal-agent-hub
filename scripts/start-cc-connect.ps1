# 启动 cc-connect：从 .env 注入飞书凭证到本进程环境变量，然后以 D 盘配置运行
# 前台调试:  powershell -ExecutionPolicy Bypass -File scripts\start-cc-connect.ps1
# 开机自启由 scripts\register-tasks.ps1 注册（隐藏窗口，日志写到 logs\cc-connect.log）
param([switch]$Background)

$ErrorActionPreference = 'Stop'
$Hub = Split-Path -Parent $PSScriptRoot
$Exe = Join-Path $Hub 'tools\cc-connect\cc-connect.exe'
$Config = Join-Path $Hub 'tools\cc-connect\config.toml'
$EnvFile = Join-Path $Hub '.env'

if (-not (Test-Path $Exe)) { throw "找不到 $Exe，先按 docs\cc-connect-setup.md 完成安装" }
if (-not (Test-Path $EnvFile)) { throw "找不到 .env" }

# 只注入飞书相关变量，且只作用于本进程（不写入系统/用户环境变量）
foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
    if ($line -match '^\s*(FEISHU_[A-Z_]+)\s*=\s*(.*?)\s*$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}
foreach ($k in 'FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'FEISHU_OWNER_OPEN_ID') {
    if (-not [Environment]::GetEnvironmentVariable($k, 'Process')) { throw ".env 中 $k 为空" }
}

New-Item -ItemType Directory -Force (Join-Path $Hub 'logs') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Hub 'tools\cc-connect\data') | Out-Null
Set-Location $Hub

if ($Background) {
    $log = Join-Path $Hub 'logs\cc-connect.log'
    $p = Start-Process -FilePath $Exe -ArgumentList '--config', "`"$Config`"" -WorkingDirectory $Hub `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err" -NoNewWindow -Wait -PassThru
    exit $p.ExitCode
}
else {
    & $Exe --config $Config
}
