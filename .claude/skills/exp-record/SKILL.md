---
name: exp-record
description: 起草 Notion「实验记录」：一个实验阶段一条，从项目状态、工作会话记录、git 提交里整理证据，写成草稿并作为检查点提交给用户，用户「通过」后由主控写入 Notion。Use when the user says 起草实验记录/记一下这个实验阶段/整理成实验记录, or when scripts/exp_draft.py runs after triage detects a stage conclusion.
---

# 起草实验记录（一个阶段一条）

输入：项目名、阶段名（例如「O17–O19 消融」「inv 演化第 1 轮」）、触发它的证据摘要（可能为空）。
**全程只读**：不写 Notion，不改代码仓库，不给工作会话发消息。唯一的写操作是草稿文件和提交检查点。

## 1. 收集证据（只读）
- `state/<项目>.md`：「今日动态」「记录」里和这个阶段相关的行（保留 7 天内的）。
- `projects/<项目>.md`：背景、进展来源、访问限制。项目页或 `CLAUDE.instance.md` 写了访问限制的（例如禁止 ssh 某台服务器），严格遵守，只用本地会话记录和本地文件。
- 工作会话记录：`events/sessions/*.json` 里该项目的 `transcript_path`（本机），读尾部相关的部分；只摘取结论、数字、配置，不整段搬运。
  - 远程项目（项目页有 `remote_outbox`）：可以用 `ssh <remote_outbox.ssh> git -C <仓库> log …`、`ssh <remote_outbox.ssh> tail …` 只读查看。
- git：`git -C <本地仓库> log --since=<阶段开始> --oneline`，以及相关提交的说明。
- Notion（只读）：用 `notion-query-data-sources` 查**实验记录**库（`config/hub.yaml` 的 `notion.experiments`），看这个项目是否已有同一阶段的记录（有就改为起草「更新」而不是新建）；查**项目**库（`notion.projects`） 找到对应的 Notion 项目名（对照 `projects/<项目>.md` 的 `notion_projects`）。

## 2. 写草稿 `drafts/experiments/YYYY-MM-DD-<项目>-<阶段 slug>.md`
字段与 Notion 实验记录库一一对应：

```markdown
---
实验: <项目简称>：<阶段名>
状态: 待验证 | 已完成          # 结论已经过我确认才算已完成；默认待验证
正确性: 未检查 | 通过 | 未通过 | 无明确结论
所属项目: <Notion 项目名>
运行日期: YYYY-MM-DD           # 阶段的主要运行日期
代码与日志: <分支 / 提交 / 日志路径；能给 URL 就给 URL>
操作: 新建 | 更新 <已有记录的标题或 URL>
---
## 假设
## 基线
## 环境与配置
## 结果与指标        # 表格优先；每个数字注明来源（提交、日志、会话）
## 结论
## 下一步行动
## 证据等级
- 已验证事实：…
- 初步证据：…
- 假设 / 推断：…
```
- 区分「已验证事实 / 初步证据 / 假设」（我的 Notion 规则）。没有证据的不要写成事实。
- 中文，术语保留英文；具体到数字和配置；不写空话。

## 3. 交回结果（由 `exp_draft.py` 提交检查点）
不要自己调用 checkpoint.py。最终回复**只输出**下面这个 JSON（不要其他文字），`exp_draft.py` 会据此提交检查点：
```json
{"draft": "<草稿的绝对路径>", "title": "实验记录：<阶段名>",
 "summary": "<3–5 行：这个阶段做了什么、核心结论、证据等级、对不上的数据>",
 "operation": "新建 | 更新 <已有记录>", "notion_project": "<Notion 项目名>", "experiment": "<实验标题>"}
```
起草失败（证据不足等）时输出 `{"error": "<原因>"}`。

## 主控收到「通过」之后（飞书主控会话）
- 按草稿 frontmatter 和各节，用 Notion MCP 在实验记录库新建（或更新）这一条，`所属项目` 关联到对应项目页；写完把 Notion 链接回复给我，并 `triage.py --resolve`。
- 我提修改意见 → 主控改草稿文件，把改动给我看，确认后再写入。
