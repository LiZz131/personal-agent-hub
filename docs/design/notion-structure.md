---
title: "Notion 工作台结构（可选集成）"
---

# Notion 工作台结构

> 公开版说明：Notion 集成是**可选的**。框架里用到 Notion 的地方只有三处：`daily-brief`（读任务库）、`exp-record`（写实验记录库）、检查点卡片里「建议的 Notion 变更」（使用者「通过」后由主控写入）。
> 不配置 `config/hub.yaml` 的 `notion.*` 就全部跳过。下面是这些 skill 假设的库结构；你可以照着建库，或者改 skill 适配自己的结构。
>
> Notion is optional. Only `daily-brief` (reads tasks), `exp-record` (writes experiment records) and the
> "suggested Notion changes" in checkpoint cards use it. Leave `notion.*` empty in `config/hub.yaml` to disable.

## 配置

通过官方 Notion MCP（`.mcp.json` 里的 `https://mcp.notion.com/mcp`）访问。授权后，把各库的 data source id（形如 `collection://<uuid>`，可以用 `notion-search` / `notion-fetch` 查到）填进 `config/hub.yaml`：

```yaml
notion:
  tasks: collection://<任务库 data source id>
  experiments: collection://<实验记录库 data source id>
  projects: collection://<项目库 data source id>
```

查询数据库要开放 `notion-query-data-sources`，否则只能靠搜索，可能漏掉条目。无头模式下用 Notion MCP 可能在结束后不退出：所有无头调用都要加 timeout。

## 库结构（skills 假设的字段）

**任务**（`notion.tasks`）：唯一的正式待办清单。每条任务只表达一个可执行的下一步，并写出可检查的完成标准。

| 属性 | 类型 / 选项 |
|---|---|
| 任务 | title |
| 状态 | status。to_do：收集箱、下一步、暂缓；in_progress：进行中、待验证、已阻塞；complete：已完成 |
| 优先级 | select：最高 / 高 / 中 / 以后再说 |
| 类型 | select：工程开发 / 实验 / 阅读研究 / 写作 / 规划决策 / 行政事务 |
| 所属项目 | relation → 项目 |
| 关联实验 | relation → 实验记录 |
| 检查点 | relation → 任务检查点 |
| 计划开始、截止日期 | date |
| 下一步行动、完成标准 | text |
| 相关资料 | url |
| 创建时间、最后更新 | created_time、last_edited_time |

**项目**（`notion.projects`）：项目 / 研究方向的索引；任务和实验关联到项目，而不是重复抄写背景。

| 属性 | 类型 / 选项 |
|---|---|
| 项目 | title |
| 状态 | status。to_do：计划中、已暂停；in_progress：进行中；complete：已完成 |
| 领域 | select（按你的方向自定） |
| 项目目标、当前里程碑 | text |
| 复盘日期 | date |

hub 里的 `projects/<p>.md` 用 frontmatter 的 `notion_projects`（名字 + 页面 url）对应到这里的项目。

**实验记录**（`notion.experiments`）：一个实验阶段一条，由 `exp-record` skill 起草、使用者确认后写入。

| 属性 | 类型 / 选项 |
|---|---|
| 实验 | title |
| 状态 | status。to_do：想法、计划中；in_progress：进行中、待验证；complete：已完成 |
| 正确性 | select：未检查 / 通过 / 未通过 / 无明确结论 |
| 所属项目 | relation → 项目 |
| 假设、基线、环境与配置、结果与指标、结论、下一步行动 | text |
| 代码与日志 | url |
| 运行日期 | date |

**任务检查点**（可选）：把复杂任务拆成可勾选的小步骤。

| 属性 | 类型 |
|---|---|
| 检查点 | title |
| 已完成 | checkbox |
| 顺序 | number |
| 说明与验收标准 | text |
| 所属任务 | relation → 任务（与任务的「检查点」双向关联） |

## 工作流约定（skills 遵守）

- **任务只记录下一步行动**；项目记录目标和阶段。
- **状态流转**：收集箱 → 下一步 → 进行中 → 待验证 → 已完成，旁支是已阻塞和暂缓。
- **「已完成」和「待验证」要区分**：实验结果未验证时不标记为结论，未知的截止日期不自动填写。
- **写入前先列变更清单**，确认后才写；区分已验证事实、初步证据、假设和待确认状态。唯一的例外是往「收集箱」新增条目。
