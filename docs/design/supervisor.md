---
title: "主控 agent + 工作 agent 设计"
status: 已实现（M1–M4 + 自动运维）
---

# 主控 agent + 工作 agent

> 公开版说明：本文是作者私有实例里设计文档的通用版本，去掉了具体项目、服务器和 Notion id；示例中的项目名、路径都是虚构的。
> Public note: generic version of the design doc from the author's private instance. Project names and paths in examples are fictional.

## 0. 设计决策
| # | 决策 |
|---|---|
| S1 | 两层结构：**主控 agent**（飞书里的 Personal Agent）+ **工作 agent**（各项目目录里的 Claude Code 会话）。使用者只需要对接主控 |
| S2 | 「审批」指**宏观审批**：剧本、阶段成果、实验结论、Notion 变更等，由使用者决定。**工具许可**风险不大，直接放行，不打扰使用者 |
| S3 | 使用者**既会在终端里直接和工作 agent 对话，也会在飞书里通过主控指挥它**，两条路都要成立 |
| S4 | 检查点：有的项目有固定流程和关卡（例如视频制作：剧本 → 配音 → 渲染），有的没有（研究项目）。每个 hook 事件都是**潜在**的检查点，由主控判断它是不是大节点：不是大节点就只更新自己掌握的项目状态，或者在已有消息上追加，不发新消息 |
| S5 | 先做简单版本：**一个主控会话** |
| S6 | 原始通知不再直接推给使用者，由主控决定要不要告诉他 |
| S7 | 分诊用 **Haiku** 无头运行（`config/hub.yaml` 的 `models.triage`），判定为大事再交给主控会话 |
| S8 | 分诊标准**逐步对齐**：使用者回复「这个不用告诉我」或「这个该告诉我」，主控把它记进该项目的分诊规则 |

## 1. 角色
```
   工作会话的 hook 事件（本地 + 远程）
              │  不再直接推送给使用者
              ▼
   分诊（Haiku，无头）：读事件 + 项目状态，三选一
     ├─ ① 静默：只更新项目状态（多数事件）
     ├─ ② 追加：原地更新该项目当天的状态卡，不提醒
     └─ ③ 新消息：宏观问题 / 检查点，需要使用者决定（提醒）
              │
   使用者 ⇄ 主控（飞书，单一会话）      使用者 ⇄ 工作 agent（终端）
              │                                   │
              └────── 指示经跨会话消息送达 ─────────┘
```

**主控是一个角色，有两种执行形态，共用同一份记忆：**
- **主控会话**：cc-connect 里和使用者对话的那个 Claude Code 会话，工作目录是 hub，用默认模型。负责回答、展示产物、派活、转达决定。
- **分诊运行**：每个事件触发一次 `claude -p --model haiku`，无头、不保存会话，只读事件和状态文件，然后写状态、更新卡片或发消息。

两者都**不靠对话上下文记事**，所有状态都写在文件里（第 3 节）。所以主控会话 `/new` 之后也不会失忆。

**工作 agent**：项目目录里的 Claude Code 会话，有两种来源：
- 使用者在终端里打开的会话（交互式）；
- 主控派活启动或续跑的会话（`claude --bg`，后台）。

## 2. 会话登记表 `events/sessions/*.json`
主控要知道每个工作会话现在是什么状态，才能决定怎么把指示送进去。登记表由 hook（`scripts/cc_hook.py`）维护，全部是确定性脚本：

| hook | 记录 |
|---|---|
| `SessionStart` | `open`，加上 host、cwd、project、transcript 路径、驱动方（终端或主控） |
| `UserPromptSubmit` | `busy`，最后活动时间，本轮由谁发起 |
| `Notification` | `waiting`（权限确认、空闲等待输入） |
| `Stop` | `idle`（本轮结束，在等输入） |
| `SessionEnd` | `closed` |

- 会话归属哪个项目：cwd 落在 `projects/<p>.md` frontmatter 的 `local_paths` 之下。
- 远程会话的登记事件也走 `~/.hub-outbox`，再经事件流传回本机。
- 只有 open 没有 closed 的会话（终端被直接关掉）：transcript 超过 6 小时没有写入，就视为 closed。

## 3. 项目状态文件 `state/<p>.md`
这是主控「对这个项目的控制」，分诊运行和主控会话都读写它。它和 `projects/<p>.md` 分开：后者是项目档案，由使用者确认；前者由 agent 自由维护，变化频繁，不进 git。
**分诊规则**是使用者的反馈，需要长期保留，所以放在进 git 的 `projects/<p>.md` 里（「分诊规则」一节），不放在状态文件中。

```markdown
---
project: example-project
updated: 2026-01-15T14:05
status_card: {message_id: om_xxx, date: 2026-01-15}
---
## 工作会话
- 3f2a9c1e | 终端 | idle | example-project | 「调度策略 ablation」| 14:02

## 待我决定
- #example-project-7 ablation 结论待审 | 产物 C:\...\report.md | 飞书消息 om_yyy

## 今日动态（追加，状态卡据此渲染）
- 13:40 修复 benchmark 脚本的计时，提交 a1b2c3
- 14:02 ablation 跑完，等待审阅
```

## 4. 分诊（`scripts/triage.py`）
- **触发**：`hub_notify.deliver` 不再直接发卡片，而是先记入 `events/log.jsonl`，再启动分诊。同一项目短时间内的多个事件合并成一次分诊。
- **输入**：事件（本轮动作、最后输出、状态分类，由 `cc_hook` 提取）、`state/<p>.md`、`projects/<p>.md` 的「分诊规则」。
- **输出**是一个 JSON 动作，由脚本执行，Haiku 本身不调用飞书：
  ```json
  {"items": [{"event_id": "<事件 id>", "action": "silent|append|notify", "line": "14:02 ablation 跑完，等待审阅"}],
   "notify": null 或 {"title": "≤20 字标题", "message": "给使用者的消息（结论先行）"}}
  ```
  silent 只记进内部记录；append/notify 写进「今日动态」并原地更新当天的状态卡；notify 另发一条提醒。
- **判断基准**：
  - 有固定流程的项目：到达关卡 → 提醒；其他 → 追加或静默。
  - 没有固定流程的项目：只有在需要方向性决定、有阶段性成果需要验收，或者卡住需要人出手时，才提醒。
  - 优先遵守「分诊规则」。
- **兜底**：分诊失败或超时，就照旧发原始通知卡片，标注「分诊失败」，不让事件丢失；同时开故障单（第 13 节）。
- **成本**：Haiku 每次读几 KB，按每天几十个事件估计，额度消耗很小。

## 5. 飞书消息
- **状态卡**：每个项目每天一张，第一个 `append` 时创建，之后**原地更新**（PATCH 接口），更新时不提醒。
- **提醒 / 检查点消息**：新消息，会提醒。标题带标签 `#<项目>-<序号>`，包括：做了什么、要决定什么、产物（文件直接附上，长文可以用 `md-to-feishu` 转飞书文档）、建议的 Notion 变更（第 7 节）。
- **回复某条消息时**：cc-connect 会把被引用消息的内容交给主控会话，主控从 `#<项目>-<n>` 标签找到项目、条目和工作会话。没有标签的消息，主控根据内容和 state.md 推断，不确定就问。

## 6. 双向对话
**本机：用 Claude Code 原生的跨会话消息。**（文档 https://code.claude.com/docs/en/cross-session-messaging）
- 本机所有 Claude Code 会话（终端里的交互式会话、`claude --bg` 后台会话）都有收件地址。主控用 `scripts/sessions.py` 找到名字，用 `SendMessage` 发过去。
- 对方**忙**时，消息在它下一次工具调用之间读到；对方**空闲**（在等输入）时，**自动开始新一轮**。
- 消息在对方的会话记录里显示为 `<cross-session-message from-name="…">`，终端里也看得到是主控转达的。
- **反方向**：如果使用者在终端里直接回答了一个检查点，分诊会从后续事件里看出来，把它标为已处理，不会再问一遍。

| 工作会话状态 | 主控怎么送达 |
|---|---|
| 正在运行（交互式或后台，忙或空闲） | `SendMessage` 给它的名字 |
| 已关闭 | `worker.py resume <会话 id> "<消息>"`：在后台续跑；随时可以 `claude attach` 接手 |
| 还没有 | `worker.py new <项目> "<任务>"` |

**远程项目**（`projects/<p>.md` 里有 `remote_outbox`）：跨会话消息只在同一台机器内有效。主控经一次 SSH 在服务器上起一个临时的无头 Haiku 会话，由它在那台机器上用 SendMessage 把消息**原样**转给目标会话（`worker.py send … --remote <项目>`），空闲会话同样会被唤醒。
- 远程会话列表：`sessions.py <项目> --remote`（`claude agents --json` over SSH）。
- 远程派活 / 续跑：`worker.py new|resume … --remote`（`claude --bg`，提示词经 stdin 传过去）。
- 远程检查点：服务器上的 `~/.hub/bin/checkpoint.py` 写发件箱 → 事件流（`scripts/remote_event_stream.py`）取回 → 本机用 scp 取回产物（≤ 30 MB）→ 飞书。
- 部署：`scripts/deploy_remote.py`（cc_hook、checkpoint、远程版 checkpoint skill；不改服务器的 settings.json）。服务器上不放任何 hub 密钥。

## 7. 检查点与 Notion（可选）
- 检查点 = 一个「待我决定」的条目。工作 agent 用用户级 skill `checkpoint`（`~/.claude/skills/checkpoint/`，源码在 `user-skills/checkpoint/`）主动提交：写一个检查点事件，附上产物路径和问题，然后停下来等结论。
- **写入 Notion 遵守「先列变更清单，确认后再写」**：检查点消息里附上建议的 Notion 变更，**使用者回复「通过」就同时批准产物和这份变更**，主控随后用 Notion MCP 写入。回复「打回 + 意见」的话，意见转给工作 agent，Notion 不动。
- Notion 库的结构见 `notion-structure.md`；库 id 配在 `config/hub.yaml` 的 `notion.*`，不配置就不启用。

### 关卡定义
- 不写死流程。哪些节点算关卡，先由分诊按事件判断，再随使用者的反馈逐步沉淀到 `projects/<p>.md` 的「分诊规则」一节。

## 8. 派活：`scripts/worker.py` 和 `scripts/sessions.py`
- `sessions.py [项目]`：合并 `claude agents --json`（正在运行的会话、状态、名字）和会话登记表（最近关闭的会话），按项目列出。
- `worker.py new <项目> "<任务>" [--cwd] [--name] [--model] [--isolate]`：在项目目录里 `claude --bg --permission-mode auto` 启动后台会话。
  - 默认直接改工作副本；`--isolate` 时进独立的 git worktree。
  - 后台会话的 hook 照常触发，所以它的事件同样进分诊。
- `worker.py resume <会话 id> "<消息>"`：只用于已关闭的会话；正在运行的会话直接发消息。
- `worker.py stop <名字或短 id>`。
- 已知问题：后台会话遇到启动对话框（例如项目里新的 MCP 服务器）或权限确认时会 `blocked`，需要 `claude attach` 处理。
- 有访问限制的服务器（跳板机、禁止直接 ssh 等）写在 `CLAUDE.instance.md` 的「项目特例」里，主控和工作 agent 都必须遵守。

## 9. 工具许可
- 工作会话使用 `auto` 权限模式，加几条 deny 规则兜底（`git push --force`、`git reset --hard`、删除项目目录以外的文件等），写进用户级或项目级设置。
- 仍然弹出的许可请求 → `Notification(permission_prompt)` 事件 → 分诊。只有被判定为危险的才提醒，并转述成一句人话。

## 10. 主控会话的约定
写在 `CLAUDE.md` 的「主控 agent（项目事件）」一节：状态查询读 `state/<p>.md`；回复带标签的消息 → 转达给对应会话；「这种不用告诉我 / 要告诉我」→ 追加到分诊规则；派活用 `worker.py`。

## 11. 实现阶段
| 阶段 | 内容 |
|---|---|
| **M1** | 会话登记表 + state.md + 分诊 + 状态卡 + 兜底；关掉原始通知 |
| **M2** | 主控会话约定 + 引用回复路由 + 跨会话消息（本地）+ `worker.py` / `sessions.py` |
| **M3** | 检查点 skill + 产物展示 + Notion 写回 |
| **M4** | 远程项目：事件流 + 远程派活 / 转达 |
| 之后 | 每天 08:00 早报（`daily-brief` skill）基于各 state.md 生成；实验记录草稿（`exp-record` skill + `scripts/exp_draft.py`） |

## 13. 自动运维（故障单）

中枢自身出错时形成「报错 → 故障单 → 修复 → 如实汇报」的闭环，实现在 `scripts/incident.py`，修复流程在 `.claude/skills/ops-fix/SKILL.md`。

- **报错入口**：分诊异常、hook 异常、检查点附件发送失败（超限和文件不存在除外）、实验记录起草结果无法解析、远程事件流投递失败、定时任务非零退出。都调用 `incident.report()`，它只记录、绝不抛异常（自身出错写 `events/incident_errors.log`）。
- **故障单**：`ops/incidents/INC-YYYYMMDD-NN-<slug>.md`，索引 `ops/INCIDENTS.md` 自动生成。各节：现象（自动填，日志尾部已脱敏）/ 根因 / 修复 / 验证 / 时间线；frontmatter 记状态、来源、指纹、次数、处理者、提交。
- **去重**：指纹 = 来源 + 异常类型 + 最后一个本仓库栈帧。同一故障只累加次数；已修复的再次出现 → `recurring` 并重新派单。
- **派单**（后台进程）：本机正在运行的开发负责 agent（`incident.py owner` 登记，否则自动取本仓库最近活跃的非主控会话）→ 无头 Haiku 用 SendMessage 转告；没有 → `claude --bg` 起 `fix-inc-…` 修复会话。同时直接（不经分诊）发「🚨 故障」卡片。每天派单次数有上限。
- **关单**：处理者填好根因 / 修复 / 验证后 `incident.py close <id> --commit <sha> [--status fixed|wontfix|needs-user]`；脚本把这三节原文发成卡片（不改写），`needs-user` 由主控转述给使用者决定。修复只在本地提交；「✅ 已修复」卡片进「待我决定」，使用者「通过」后主控 `incident.py approve <id>` 推送。
- 早报列出未关闭和 24 小时内关闭的故障单。
