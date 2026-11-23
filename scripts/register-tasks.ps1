# 在 Windows 任务计划程序中注册 hub 定时任务（当前用户，登录后才运行，无需管理员）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\register-tasks.ps1
# 卸载: Get-ScheduledTask -TaskPath '\PersonalAgent\' | Unregister-ScheduledTask -Confirm:$false

$Runner = Join-Path $PSScriptRoot 'run-job.ps1'
$Pyw = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\pythonw.exe'
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

# 所有任务都经 pythonw 启动，不出现任何窗口：-WindowStyle Hidden 仍会闪窗并抢焦点，所以经 run_hidden.py 包一层
function Register-HubJob($Name, $Job, $Trigger) {
    $Action = New-ScheduledTaskAction -Execute $Pyw -WorkingDirectory (Split-Path -Parent $PSScriptRoot) `
        -Argument "-I scripts\run_hidden.py powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Runner`" -Job $Job"
    Register-ScheduledTask -TaskPath '\PersonalAgent\' -TaskName $Name -Action $Action `
        -Trigger $Trigger -Settings $Settings -Force | Out-Null
    Write-Host "registered \PersonalAgent\$Name"
}

# 每 2 小时处理一次 inbox（inbox 为空时不调用 claude）
$every2h = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(8) `
    -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-HubJob 'ingest-inbox' 'ingest-inbox' $every2h

# 每周日 21:00 体检
Register-HubJob 'weekly-lint' 'lint' (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 21:00)

# 订阅简报 / 周报改由 cc-connect 的定时任务运行（每次生成一个可追问的飞书会话），这里移除旧的计划任务
foreach ($old in 'daily-digest', 'weekly-digest') {
    Get-ScheduledTask -TaskPath '\PersonalAgent' -TaskName $old -ErrorAction SilentlyContinue |
        Unregister-ScheduledTask -Confirm:$false
}

# 每周一 09:00 推送本周学习计划
Register-HubJob 'study-plan' 'study-plan' (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 09:00)

# 远程 Claude Code 事件：每台服务器一条 SSH 长连接，事件到达即推送（取代原来的每分钟轮询）
Get-ScheduledTask -TaskPath '\PersonalAgent\' -TaskName 'pull-remote-events' -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false
$StreamAction = New-ScheduledTaskAction -Execute (Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\pythonw.exe') `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot) -Argument '-I scripts\remote_event_stream.py'
$StreamSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskPath '\PersonalAgent\' -TaskName 'remote-event-stream' -Action $StreamAction `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Settings $StreamSettings -Force | Out-Null
Write-Host "registered \PersonalAgent\remote-event-stream"

# 旧的自研 feishu-bot 已由 cc-connect 取代：若曾注册过则移除
Get-ScheduledTask -TaskPath '\PersonalAgent\' -TaskName 'feishu-bot' -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false

# cc-connect：登录后启动，常驻；崩溃后 1 分钟自动重启；隐藏窗口
$Hub = Split-Path -Parent $PSScriptRoot
# 用 pythonw 启动器而非隐藏的 PowerShell：后者在后台任务里会收到控制台 Ctrl+C 事件导致 cc-connect 退出
$CcAction = New-ScheduledTaskAction -Execute (Join-Path $Hub '.venv\Scripts\pythonw.exe') -WorkingDirectory $Hub `
    -Argument '-I scripts\cc_connect_launcher.py'
$CcSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskPath '\PersonalAgent\' -TaskName 'cc-connect' -Action $CcAction `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Settings $CcSettings -Force | Out-Null
Write-Host "registered \PersonalAgent\cc-connect (立即启动: Start-ScheduledTask -TaskPath '\PersonalAgent\' -TaskName cc-connect)"
