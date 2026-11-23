---
title: "Example Project（示例项目：推理调度实验）"
type: project
# 一行说明项目在哪 / where the project lives (free text)
location: user@gpu-box:~/example-project（本地 C:\Users\<you>\code\example-project）
status: active          # active | paused | done
# 工作会话的 cwd 落在这些路径下时，hook 事件归到本项目；远程路径（/ 开头）也可以写
# Hook events whose session cwd is under one of these paths are attributed to this project.
local_paths:
  - 'C:\Users\<you>\code\example-project'
  - '/home/user/example-project'
# 可选：远程项目。服务器上的 hook 写发件箱，scripts/remote_event_stream.py 经 SSH 取回。
# ssh 是 ~/.ssh/config 里的主机别名。没有远程部分就删掉这一行。
# Optional: remote project. ssh = host alias from ~/.ssh/config. Remove if not remote.
remote_outbox: {ssh: gpu-box, dir: '~/.hub-outbox'}
# 可选：对应的 Notion 项目（早报、实验记录用）/ optional linked Notion project pages
notion_projects:
  - {name: "Example Project", url: "https://www.notion.so/<your-page-id>"}
updated: 2026-01-15
---

# Example Project（示例项目：推理调度实验）

<!-- 复制到 projects/<项目名>.md 使用；文件名就是项目名（分诊标签 #<项目名>-<n>、state/<项目名>.md）。
     Copy to projects/<name>.md; the file name is the project name used in tags and state files. -->

## 当前状态
<!-- 由 agent 根据进展来源起草，你确认 / drafted by agents, confirmed by you -->
- **目标**：评估两种调度策略在混合长度请求下的 TTFT / 吞吐差异
- **最近进展**：完成 baseline 跑分；新策略的实现在分支 `sched-v2`
- **卡点**：GPU 机时有限
- **下一步**：在 3 种负载下跑 ablation

## 环境与访问
- **服务器**：`gpu-box`（ssh 别名），本机可以免密登录。
- **Claude Code**：在服务器上运行；本地也有会话。

## 进展来源
| 来源 | 位置 | 代价 |
|---|---|---|
| 远程 git log 和工作区状态 | `ssh gpu-box 'cd ~/example-project && git log …'` | 一次 SSH，只读 |
| 本地 Claude Code 会话 | `~/.claude/projects/<编码后的路径>/` | 本地读取 |

## 相关知识
- [[speculative-decoding]] / [[continuous-batching]]

## 分诊规则
<!-- 主控分诊时优先遵守。来自你的反馈（「这个不用告诉我」「这种要告诉我」），由主控原样追加：- YYYY-MM-DD <规则>
     Triage rules: your feedback, appended verbatim by the supervisor. Triage reads this section first. -->
- 2026-01-15 单纯的 lint / 格式修复不用告诉我
- 2026-01-16 实验跑完出了指标要告诉我

## 决策日志
<!-- 只追加，最新在下 / append-only, newest last -->
- 2026-01-15 建档：远程只做只读 SSH 查询，不在服务器上存放 hub 的密钥。
