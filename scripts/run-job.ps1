# 无人值守地运行一个 hub 任务（供任务计划程序调用，也可手动跑）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\run-job.ps1 -Job lint
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('ingest-inbox', 'lint', 'study-plan', 'daily-digest', 'weekly-digest')]
    [string]$Job
)

$ErrorActionPreference = 'Stop'
$Hub = Split-Path -Parent $PSScriptRoot
Set-Location $Hub
New-Item -ItemType Directory -Force (Join-Path $Hub 'logs') | Out-Null
$Log = Join-Path $Hub ("logs\{0}-{1}.log" -f (Get-Date -Format 'yyyy-MM-dd_HHmm'), $Job)

switch ($Job) {
    'ingest-inbox' {
        # inbox 为空就不启动 claude，省 token
        $pending = @(Get-Content (Join-Path $Hub 'inbox\links.md') -Encoding UTF8 |
            Where-Object { $_ -match '^\s*https?://' }).Count
        $files = @(Get-ChildItem (Join-Path $Hub 'inbox') -File |
            Where-Object { $_.Name -notin @('links.md', 'done.md', '.gitkeep') }).Count
        if ($pending + $files -eq 0) { "inbox empty" | Out-File $Log -Encoding utf8; exit 0 }
        $prompt = '使用 ingest skill 处理 inbox 中的所有条目。这是无人值守运行：遇到需要我确认的事项写入汇报，不要等待。'
    }
    'lint' {
        $prompt = '使用 lint skill 做一次健康检查。这是无人值守运行：只写报告，不做修复。'
    }
    'daily-digest' {
        # 先轮询订阅源（纯脚本，不耗额度）；没有新候选就不启动 claude、也不推送
        $py = Join-Path $Hub '.venv\Scripts\python.exe'
        $poll = & $py -I scripts\poll_feeds.py | Select-Object -Last 1
        $poll | Out-File "$Log.poll" -Encoding utf8
        $pj = $poll | ConvertFrom-Json
        if ($pj.total -eq 0 -and $pj.errors.Count -eq 0) { "no new candidates" | Out-File $Log -Encoding utf8; exit 0 }
        $prompt = "使用 digest skill 的 A 节，基于 $($pj.file) 生成今日订阅简报。这是无人值守运行：最终回复就是要推送给我的简报正文。"
    }
    'weekly-digest' {
        $prompt = '使用 digest skill 的 C 节生成本周订阅周报（反馈统计 + 主动网络检索 + 兴趣画像调整提议）。这是无人值守运行：不修改 interests.md，最终回复就是要推送给我的周报正文。'
    }
    'study-plan' {
        $prompt = '按 .claude/roles/planner.md 的「学习规划」角色工作：整理 learning/queue.md，给出本周建议学习的 3 条（附原因、预计用时、前置概念），并列出超过 30 天未动的条目。这是无人值守运行：只更新队列排序和写报告，不删除条目。最终回复就是要推送给我的周学习计划。'
    }
}

# acceptEdits: 允许在仓库内读写；Bash 只放开 fetch 脚本和复制；不允许发消息/提交
# 用 Start-Process 而不是直接调用：PS 5.1 会把原生命令的 stderr 当成错误记录，配合 Stop 会直接中断脚本
$ErrorActionPreference = 'Continue'
$claude = (Get-Command claude).Source
$empty = Join-Path $Hub 'logs\.empty-stdin'
if (-not (Test-Path $empty)) { New-Item -ItemType File $empty | Out-Null }
$argList = @(
    '-p', "`"$prompt`"",
    '--model', 'sonnet',
    # 无人值守任务不保存会话记录，免得出现在飞书 /list 里
    '--no-session-persistence',
    '--permission-mode', 'acceptEdits',
    '--allowedTools', '"Bash(.venv/Scripts/python -I scripts/fetch.py:*)"', '"Bash(.venv/Scripts/python -I scripts/lint.py:*)"', '"Bash(cp:*)"',
    '"Bash(git status:*)"', '"Bash(git diff:*)"', 'WebFetch', 'WebSearch'
)
# 与飞书守护进程共用同名 mutex：同一时刻只有一个 Claude 在改 wiki
$mutex = New-Object System.Threading.Mutex($false, 'Local\PersonalAgentClaude')
try { [void]$mutex.WaitOne() } catch [System.Threading.AbandonedMutexException] { }
try {
    $p = Start-Process -FilePath $claude -ArgumentList $argList -WorkingDirectory $Hub `
        -RedirectStandardInput $empty -RedirectStandardOutput $Log -RedirectStandardError "$Log.err" `
        -NoNewWindow -Wait -PassThru
}
finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
if ((Test-Path "$Log.err") -and (Get-Item "$Log.err").Length -eq 0) { Remove-Item "$Log.err" }

# 推送结果到飞书（未配置 .env 时跳过）
$py = Join-Path $Hub '.venv\Scripts\python.exe'
if (Test-Path (Join-Path $Hub '.env')) {
    $titles = @{ 'ingest-inbox' = '📚 Inbox 入库完成'; 'lint' = '🩺 每周体检'; 'study-plan' = '📖 本周学习计划'; 'daily-digest' = '📰 今日订阅'; 'weekly-digest' = '🗞 订阅周报' }
    if ($p.ExitCode -eq 0) {
        & $py -I scripts\feishu_notify.py --title $titles[$Job] --file $Log
    }
    else {
        $detail = if (Test-Path "$Log.err") { "$Log.err" } else { $Log }
        & $py -I scripts\feishu_notify.py --title "❌ $Job 失败 (exit $($p.ExitCode))" --file $detail
        & $py -I scripts\incident.py report "job:$Job" "定时任务 $Job 失败（exit $($p.ExitCode)）" --file $detail | Out-Null
    }
}
exit $p.ExitCode
