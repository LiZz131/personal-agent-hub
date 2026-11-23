---
name: ops-fix
description: 处理 Personal Agent 中枢自身的故障单（ops/incidents/INC-*.md）：复现报错、查根因、做最小修复、验证、提交，把根因/修复/验证如实写进故障单后关单，由脚本把结果发到飞书。Use when a message says 「运维（自动）：出现了新的故障」, or the user asks to fix/look into an INC-* incident.
---

# 修复故障单（自动运维）

你收到的是 `scripts/incident.py` 自动开的故障单。目标：**找到真正的根因并修好**，然后如实记录。
可能是开发负责 agent（我正在用的终端会话）收到转告，也可能是专门起的后台修复会话 `fix-inc-…`。

## 1. 读现场（只读）
- 故障单：现象、来源、出现次数、日志路径。`ops/INCIDENTS.md` 里看有没有相关的旧单（同一处反复出问题要一起看）。
- 日志都在 `events/`：`triage_errors.log`、`hook_errors.log`、`incident_errors.log`、`exp_drafts/`、`stream.log`、`logs/`（定时任务）。
- 相关代码和最近的提交：`git log --oneline -15 -- <文件>`。
- 设计约定见 `docs/design/supervisor.md` 和 `CLAUDE.md`。

## 2. 复现 → 根因
- 尽量复现（最小命令或最小输入），在故障单「根因」写清：根因是什么、证据（日志行、`file:line`、复现命令及输出）。
- 区分「已验证」和「推断」。复现不了就写复现不了，以及为什么仍然认为是这个原因。
- 外部原因（飞书/Notion 接口限制、网络、权限、额度）也是根因：修复是让代码正确处理它（重试、降级、清楚的报错），而不是假装没发生。

## 3. 修复
- **最小修复**，只改和根因有关的代码；顺手看同类代码有没有同样的问题。
- 出错路径要「失败可见」：不能为了不报错就吞掉异常。
- **不要做**：读或改 `.env`、`.secrets/`；放宽权限（改 settings 的 allow/deny）；改 `CLAUDE.md`；写 Notion；`git push`（任何推送都由我确认后主控来做）、`reset --hard`；违反 `CLAUDE.instance.md` 或项目页写明的访问限制。
- 需要这些，或者修复涉及方向性取舍（改设计、删功能、动用户数据）→ 不要动手：在故障单写清楚方案和取舍，`close <id> --status needs-user`，主控会把它发给我决定。

## 4. 验证
- 重跑复现命令，确认不再出错；能真实走一遍的就真实走一遍（例如真的发一次飞书测试消息），并在「验证」写明命令和结果。
- 改了 Python 至少 `python -I -c "import ast; ast.parse(...)"`，能 import 的模块 import 一次。
- 没验证到的部分写「未验证：…」。

## 5. 提交
- 只提交你改的文件（带路径提交，不要 `git add -A`，工作区里可能有我没提交的东西）：
  ```
  git add <文件…> ops/ && git commit -m "fix(<INC-id>): <一句话>" -m "<根因一句话>" -- <文件…> ops/
  ```
  提交说明末尾加 Co-Authored-By 行（按当前会话的提交署名约定）。
- **不要推送**：修复只在本地提交，关单后我在飞书确认，主控再推送。

## 6. 关单（如实汇报）
- 先把「根因」「修复」「验证」三节写完整（脚本会检查，缺了不让关）。
- `.venv/Scripts/python -I scripts/incident.py close <INC-id> --commit <sha>`。脚本把这三节**原文**发成「✅ 已修复」卡片，所以写的时候就当作是直接给我看的汇报：具体、诚实、不夸大。
- 修不了：`--status needs-user`（需要我决定）或 `--status wontfix`（不是 bug / 不值得修，写明理由）。
- 修复后再次出现的单（`recurring`）：说明上次的修复为什么不够。
