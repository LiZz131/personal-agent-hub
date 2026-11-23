# 安装 / 更新 Personal Agent Hub 的本机组件（幂等，可重复运行）。
# Install / update the local pieces of the hub (idempotent; safe to re-run).
#
# 用法 / Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1                 # 全部步骤 / all steps
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -DryRun         # 只打印将做的事 / print only (also -WhatIf)
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -UserClaudeDir .cache\fake-claude -SkipVenv
#
# 步骤 / Steps:
#   1. config\hub.yaml、.env、CLAUDE.instance.md、interests.md、subscriptions.yaml：不存在时从示例复制
#      copy example configs if missing
#   2. scripts\setup-venv.ps1：建 .venv 并安装依赖 / create .venv and install requirements (uv)
#   3. <UserClaudeDir>\settings.json：合并 5 个 hook（SessionStart / UserPromptSubmit / Notification / Stop / SessionEnd
#      -> <hub>\.venv\Scripts\python.exe -I <hub>\scripts\cc_hook.py），写入前备份
#      merge the 5 user-level hooks, with a backup
#   4. <UserClaudeDir>\skills\checkpoint\SKILL.md：由 user-skills\checkpoint 渲染（{{HUB}} -> hub 路径）
#   5. tools\cc-connect\config.toml：不存在时由 config.toml.example 渲染 / render if missing
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Hub = '',
    [string]$UserClaudeDir = '',
    [string]$Python = '',
    [switch]$DryRun,
    [switch]$SkipVenv
)

$ErrorActionPreference = 'Stop'
# PS 5.1 的 param 默认值里拿不到 $PSScriptRoot，在这里补默认值
if (-not $Hub) { $Hub = Split-Path -Parent $PSScriptRoot }
if (-not $UserClaudeDir) { $UserClaudeDir = Join-Path $env:USERPROFILE '.claude' }
$Hub = (Resolve-Path $Hub).Path.TrimEnd('\')
$Dry = $DryRun -or $WhatIfPreference
if (-not [IO.Path]::IsPathRooted($UserClaudeDir)) { $UserClaudeDir = Join-Path (Get-Location) $UserClaudeDir }
$tag = if ($Dry) { ' [dry-run]' } else { '' }
Write-Host "Hub:            $Hub$tag"
Write-Host "User Claude dir: $UserClaudeDir"

function Copy-IfMissing($Src, $Dst) {
    $s = Join-Path $Hub $Src; $d = Join-Path $Hub $Dst
    if (Test-Path $d) { Write-Host "  已存在，跳过 / exists: $Dst"; return }
    if (-not (Test-Path $s)) { Write-Host "  缺少示例 / missing template: $Src"; return }
    if ($Dry) { Write-Host "  [dry-run] $Src -> $Dst"; return }
    New-Item -ItemType Directory -Force (Split-Path -Parent $d) | Out-Null
    Copy-Item $s $d
    Write-Host "  $Src -> $Dst"
}

Write-Host "`n==> 1. 实例配置 / instance config"
Copy-IfMissing 'config\hub.example.yaml' 'config\hub.yaml'
Copy-IfMissing '.env.example' '.env'
Copy-IfMissing 'examples\CLAUDE.instance.md' 'CLAUDE.instance.md'
Copy-IfMissing 'examples\interests.md' 'interests.md'
Copy-IfMissing 'examples\subscriptions.yaml' 'subscriptions.yaml'

Write-Host "`n==> 2. Python 环境 / venv"
if ($SkipVenv) { Write-Host "  跳过 / skipped (-SkipVenv)" }
elseif ($Dry) { Write-Host "  [dry-run] 将运行 / would run scripts\setup-venv.ps1" }
else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Hub 'scripts\setup-venv.ps1')
    if ($LASTEXITCODE -ne 0) { throw "setup-venv.ps1 失败（exit $LASTEXITCODE）" }
}

# 后面的步骤用 Python 处理 JSON 和模板（只用标准库）
if (-not $Python) { $Python = Join-Path $Hub '.venv\Scripts\python.exe' }
if (-not (Test-Path $Python)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($Dry -and $cmd) { $Python = $cmd.Source }
    else { throw "找不到 Python：$Python（先去掉 -SkipVenv 运行一次，或用 -Python 指定）" }
}
$util = Join-Path $Hub 'scripts\install_util.py'
$dryArg = @(); if ($Dry) { $dryArg = @('--dry-run') }

function Invoke-Util([string[]]$UtilArgs) {
    & $Python -I $util @UtilArgs
    if ($LASTEXITCODE -ne 0) { throw "install_util.py $($UtilArgs[0]) 失败（exit $LASTEXITCODE）" }
}

Write-Host "`n==> 3. 用户级 hook / user-level hooks"
Invoke-Util (@('hooks', '--settings', (Join-Path $UserClaudeDir 'settings.json'), '--hub', $Hub) + $dryArg)

Write-Host "`n==> 4. 用户级 skill: checkpoint"
Invoke-Util (@('render', '--src', (Join-Path $Hub 'user-skills\checkpoint\SKILL.md'),
        '--dst', (Join-Path $UserClaudeDir 'skills\checkpoint\SKILL.md'), '--hub', $Hub, '--force') + $dryArg)

Write-Host "`n==> 5. cc-connect 配置 / config.toml"
Invoke-Util (@('render', '--src', (Join-Path $Hub 'tools\cc-connect\config.toml.example'),
        '--dst', (Join-Path $Hub 'tools\cc-connect\config.toml'), '--hub', $Hub, '--slash') + $dryArg)

Write-Host "`n完成 / done.$tag"
Write-Host "下一步 / next: 填写 .env 和 config\hub.yaml，编译或下载 cc-connect（见 docs\cc-connect-setup.md），然后运行 scripts\register-tasks.ps1。"
