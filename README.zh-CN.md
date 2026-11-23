# Personal Agent Hub

[English](README.md)

基于 [Claude Code](https://code.claude.com/) 的个人知识库 + 项目主控。它本身是一套约定（`CLAUDE.md`、skills、角色、subagent）加一组 Python / PowerShell 脚本，没有自己的服务端：推理交给 Claude Code，记账交给脚本，手机端界面是飞书（Lark）。

它做两件事：

1. **LLM Wiki**（参考 Karpathy 的「LLM Wiki」：raw → wiki → schema）。链接、PDF、笔记丢进 `inbox/`，agent 把原文存进 `raw/`，写来源页，更新概念 / 实体页、index 和 log。概念页带你自己的程度标注（`my_level`），讲解深度据此调整。另外还有：按 `interests.md` 打分的每日订阅简报、待学队列、导师角色。
2. **Claude Code 会话的主控。** 本机（以及可选的远程服务器）上每个交互式或后台 Claude Code 会话，都通过用户级 hook 上报事件。一个便宜的无头模型（Haiku）给每个事件分诊：静默、在该项目当天的状态卡上追加一行、或者发消息提醒你。你在飞书里只和一个「主控」会话对话；它用 Claude Code 的跨会话消息把你的决定转达给对应的工作会话，启动或续跑后台会话，并且把记忆写在文件里，不依赖对话上下文。

作者一个人在 Windows 上每天使用。会有粗糙的地方，见[局限](#局限)。

## 功能

- **分诊代替原始通知。** hook 事件 → `events/log.jsonl` → `scripts/triage.py`（Haiku）→ 静默 / 追加到当天状态卡（原地更新，不提醒）/ 带 `#<项目>-<n>` 标签的新消息。分诊失败时兜底发原始通知，事件不丢。你的反馈（「这种不用告诉我」）原样追加到项目页的「## 分诊规则」，下次优先遵守。
- **检查点。** 工作 agent 需要验收或方向性决定时调用用户级 `checkpoint` skill。你收到一张卡片：问题、摘要、产物附件；回复「通过」或「打回 + 意见」，主控转达回那个会话。
- **早报**（`daily-brief` skill）：各项目待你决定的事、过去 24 小时动态、卡住的会话，以及（可选）Notion 任务库里该推进和快到期的任务。
- **实验记录**（`exp-record` skill、`scripts/exp_draft.py`）：分诊发现一个实验阶段有了结论时，从项目状态、会话记录、git 历史整理出草稿，作为检查点发给你，你「通过」后才写入 Notion。
- **自动运维故障单**（`scripts/incident.py`、`ops-fix` skill）：中枢自身的脚本出错时，在 `ops/incidents/` 开故障单（按指纹去重），交给正在运行的开发会话或后台修复会话；关单时把根因 / 修复 / 验证原文发给你。修复只在本地提交，你批准后才推送。
- **远程项目**（可选）：服务器上跑同一个 hook，写 `~/.hub-outbox/`；`scripts/remote_event_stream.py` 每台服务器保持一条 SSH 长连接把事件推回本机。服务器上不放任何 hub 密钥。
- **Wiki 工具**：`ingest`、`query`、`lint`、`calibrate`、`digest` 等 skill；四个角色（整理、问答、学习规划、导师）；`reader` / `clerk` / `scout` 三个固定用较便宜模型的 subagent，节省额度。

## 架构

```
  飞书（手机 / 桌面）
      │  对话、卡片、文件
      ▼
  cc-connect（hub 分支 fork）──► 主控会话：在 <hub> 里运行的 Claude Code，遵守 CLAUDE.md
      │                                  │  SendMessage / worker.py / sessions.py
      │                                  ▼
      │                         工作会话：各项目目录里的 Claude Code
      │                         （终端或 `claude --bg`；本机，或经 SSH 的远程服务器）
      │                                  │  用户级 hook：SessionStart、UserPromptSubmit、
      │                                  │  Notification、Stop、SessionEnd → scripts/cc_hook.py
      │                                  ▼
      │                         events/log.jsonl、events/sessions/*.json
      │                                  │
      │                                  ▼
      └──── 飞书 API ◄────────── scripts/triage.py（无头 Haiku）
            （状态卡、                   │ 写入
             提醒、检查点）               ▼
                                 state/<项目>.md（待我决定、今日动态）
                                 projects/<项目>.md（项目页 + 分诊规则）

  定时任务（Windows 任务计划程序 / cc-connect cron）：inbox 入库、每周体检、
  学习计划、订阅简报、早报。          可选：通过官方 Notion MCP 接 Notion。
```

## 依赖

- **Windows 10/11**（第一版只支持 Windows：PowerShell 脚本、任务计划程序、`pythonw` 等）。
- **Claude Code CLI**（`claude`），已登录。用到后台会话（`claude --bg`）、`claude agents` 和跨会话消息。
- **[uv](https://docs.astral.sh/uv/)**：管理仓库内的 `.venv`（测试过 Python 3.12）。
- **git**。
- **飞书（或 Lark）自建应用**，开启机器人能力：见 `docs/feishu-setup.md`。
- **cc-connect 的 fork，hub 分支**：<https://github.com/LiZz131/cc-connect>（基于上游 [chenhg5/cc-connect](https://github.com/chenhg5/cc-connect) v1.5.3）。用 Go 从源码编译（`scripts/build-cc-connect.ps1`），本仓库不附带二进制。见 `docs/cc-connect-setup.md`。
- 可选：**Notion**，通过官方 Notion MCP（`.mcp.json`），用于早报和实验记录。
- 可选：远程项目需要能 **SSH** 免密登录服务器。

## 安装

```powershell
git clone <本仓库> C:\path\to\hub
cd C:\path\to\hub
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -DryRun   # 先看会做什么
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

`install.ps1` 可以重复运行，它会：

1. 不存在时复制 `config/hub.example.yaml` → `config/hub.yaml`、`.env.example` → `.env`，以及 `examples/` 里的 `CLAUDE.instance.md`、`interests.md`、`subscriptions.yaml` 到仓库根；
2. 运行 `scripts/setup-venv.ps1`（用 uv 建 `.venv`，安装 `requirements.txt`）；
3. 把 5 个 hook 合并进 `~/.claude/settings.json`（先备份；已有的 `cc_hook.py` 条目原地更新，其他 hook 不动）；
4. 由 `user-skills/checkpoint/` 渲染用户级 skill `~/.claude/skills/checkpoint/SKILL.md`；
5. 不存在时由 `config.toml.example` 渲染 `tools/cc-connect/config.toml`。

参数：`-UserClaudeDir <目录>`（装到别的目录，例如测试用）、`-Python <python.exe>`、`-SkipVenv`、`-DryRun` / `-WhatIf`。

然后：

1. 按 `docs/feishu-setup.md`、`docs/feishu-project-notify.md` 填 `.env`（飞书 app id / secret、你的 open_id、通知群 chat_id）。
2. 改 `config/hub.yaml` 和 `CLAUDE.instance.md`（语言偏好、使用者档案目录、各项目的访问限制）。
3. 编译 cc-connect 并前台试运行（`docs/cc-connect-setup.md`）。
4. 注册定时任务：`scripts\register-tasks.ps1`（登录后启动 cc-connect、远程事件流、inbox 入库、每周体检、学习计划）。订阅简报和 08:00 早报作为 cc-connect 的 cron 任务运行（每次新会话），这样可以在飞书里追问；用 cc-connect 的 cron 指令创建，内容分别指向 `digest` / `daily-brief` skill。
5. 每个项目在 `projects/` 下建一页（复制 `examples/projects/example-project.md`）。远程项目运行一次 `scripts/deploy_remote.py`。

## 配置

| 文件 | 内容 | 进 git？ |
|---|---|---|
| `CLAUDE.md` | 所有 agent 遵守的框架约定 | 是（框架） |
| `CLAUDE.instance.md` | 你的个人设置和项目特例；由 `CLAUDE.md` 导入，冲突时以它为准 | 自定 |
| `config/hub.yaml` | `language`、`memory_dir`（可选的只读使用者档案）、`claude_bin`、分诊 / 转达 / 实验记录草稿用的模型、Notion data source id、cc-connect 路径。由 `scripts/hub_config.py` 读取，缺的字段用默认值 | 自定 |
| `.env` | 只放密钥（飞书凭证、chat_id、webhook）。`.claude/settings.json` 禁止 Claude Code 读取 | **绝不** |
| `interests.md`、`subscriptions.yaml` | 兴趣画像和订阅源 | 自定 |
| `tools/cc-connect/config.toml` | 由模板渲染的 cc-connect 配置；密钥以 `${VAR}` 引用 | 自定 |

## 目录约定

框架在你的实例里读写这些位置：

| 路径 | 内容 | 谁写 |
|---|---|---|
| `projects/<p>.md` | 每个项目一页。frontmatter：`local_paths`（会话 cwd → 项目）、可选 `remote_outbox: {ssh, dir}`、可选 `notion_projects`；正文包括「## 分诊规则」 | 你 / agent 起草你确认 |
| `state/<p>.md` | 主控的记忆：待我决定、工作会话、今日动态、记录 | 分诊 + 主控（不进 git） |
| `events/` | `log.jsonl`、`sessions/*.json` 会话登记表、`cards/` | hook、分诊（不进 git） |
| `ops/incidents/`、`ops/INCIDENTS.md` | 故障单和自动生成的索引 | `incident.py`、修复会话 |
| `drafts/experiments/` | 实验记录草稿 | `exp_draft.py` |
| `inbox/`、`raw/`、`wiki/`、`digests/`、`learning/`、`feeds/` | Wiki 流水线（见 `CLAUDE.md` 第 1 节） | 你、ingest、digest、各角色 |

项目名就是文件名：`projects/foo.md` ↔ `state/foo.md` ↔ 标签 `#foo-<n>`。中枢自身的故障单记在项目名 `Personal_Agent` 下。

## 局限

- **只支持 Windows。** 路径、PowerShell、任务计划程序、`.venv\Scripts` 都按 Windows 写。
- **只支持飞书。** 通知经飞书开放平台和 cc-connect 发出，还没有通知渠道的抽象层。
- **以中文为主。** `CLAUDE.md`、skills、分诊提示词、卡片都是中文。`config/hub.yaml` 里的 `language` 字段是预留的，目前没有脚本读取它。可以在 `CLAUDE.instance.md` 里要求 agent 用英文回答，但卡片和提示词仍是中文。
- **依赖较新的 Claude Code 功能**（后台会话、`claude agents --json`、跨会话消息、hook），Claude Code 版本变化可能影响行为。
- **依赖 cc-connect 的 fork。** 上游版本可能只有部分功能可用。
- **单用户。** 只有一个 owner open_id；除 cc-connect 的 `allow_from` 外没有多用户权限控制。
- **远程项目**假设服务器能用主机别名免密 SSH，并且装了 Claude Code。
- **可选部分没有测试覆盖**：Notion 集成、远程项目。
- 没有自动化测试套件，脚本靠日常使用验证。

## 许可

Apache License 2.0，见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。
