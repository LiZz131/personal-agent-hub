# 从 vendor\cc-connect（hub 分支）编译 cc-connect。
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\build-cc-connect.ps1            # 编译到 tools\cc-connect\cc-connect.new.exe
#   powershell -ExecutionPolicy Bypass -File scripts\build-cc-connect.ps1 -Install   # 编译后停服务、替换 cc-connect.exe、再启动
# 依赖：Go 在 PATH 上（或用环境变量 GO_EXE 指定 go.exe）。
param([switch]$Install)

$ErrorActionPreference = 'Stop'
$Hub = Split-Path -Parent $PSScriptRoot
$Src = Join-Path $Hub 'vendor\cc-connect'
$Go = if ($env:GO_EXE) { $env:GO_EXE } else { (Get-Command go -ErrorAction Stop).Source }
$Dir = Join-Path $Hub 'tools\cc-connect'
$Out = Join-Path $Dir 'cc-connect.new.exe'

Push-Location $Src
try {
    $commit = (git rev-parse --short HEAD).Trim()
    $base = (git describe --tags --abbrev=0 2>$null)
    if (-not $base) { $base = 'dev' }
    $dirty = if (git status --porcelain) { '-dirty' } else { '' }
    $version = "$base-hub+$commit$dirty"
    $time = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $ld = "-s -w -X main.version=$version -X main.commit=$commit -X main.buildTime=$time"
    Write-Host "==> go build $version"
    # no_web：不嵌入 Web 管理界面（需要另外用 npm 构建前端，hub 用不到）
    & $Go build -trimpath -tags no_web -ldflags $ld -o $Out ./cmd/cc-connect
    if ($LASTEXITCODE -ne 0) { throw "go build 失败（exit $LASTEXITCODE）" }
}
finally { Pop-Location }

& $Out --version

if ($Install) {
    $task = @{ TaskPath = '\PersonalAgent\'; TaskName = 'cc-connect' }
    Stop-ScheduledTask @task
    Start-Sleep -Seconds 3
    Move-Item -Force $Out (Join-Path $Dir 'cc-connect.exe')
    Start-ScheduledTask @task
    Write-Host "==> 已替换 cc-connect.exe 并重启服务"
}
