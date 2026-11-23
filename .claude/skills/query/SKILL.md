---
name: query
description: 基于个人 LLM Wiki 回答问题、讲解新概念、对比多个来源。Use when the user asks 我之前读过的/知识库里/对比一下/讲讲 X, or any question that should be answered from accumulated notes rather than from scratch.
---

# Query

1. 读 `wiki/index.md` 定位候选页；再 grep `wiki/`（含 aliases、tags）补漏。
2. 读相关 concept/entity/source 页；需要细节时回到 `raw/` 原文。
2.5 **结合网络**：wiki 不足、可能过时或话题在快速变化时，用 WebSearch / WebFetch 补充，网络来源附 URL。值得长期保留的资源列入末尾的「建议入库」清单，经我同意后再 ingest。
3. **按我的程度讲**：看相关概念页的 `my_level` 和 `## My notes`，并参考使用者档案（`config/hub.yaml` 的 `memory_dir`，未配置就跳过）里的 `identity.md`：
   - `熟练/精通`：跳过基础，直接讲差异、新机制与权衡。
   - `unknown` 或涉及我的弱项（OS/网络/概率统计）：先问一句我的程度。
   - 讲解贴着我已有的笔记来（"你笔记里理解的 X，这里的区别是…"）。
4. 回答中每个关键论断标注出处：wiki 用 `[[page]]`，网络附 URL，你自身的知识标「（wiki 外）」。
5. 对比类问题：用表格，列出维度、各来源立场、证据强弱。
6. 如果回答本身有长期价值（综述、对比、新理解），**提议**存为 `wiki/concepts/` 新页或补入已有页；我同意后再写，并追加 log `[query]`。
