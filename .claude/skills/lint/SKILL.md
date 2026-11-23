---
name: lint
description: 对个人 LLM Wiki 做健康检查：坏链接、孤立页、无出处论断、矛盾、index 遗漏、过期项目状态。Use when the user says lint/体检/检查知识库, or from the weekly scheduled run.
---

# Lint

只检查和报告；修复动作先列清单，我确认后再改（定时任务模式下只写报告）。

## 检查项
1. **坏链接**：`[[x]]` 指向不存在的页面。
2. **孤立页**：没有任何页面链接到它、且不在 index 中。
3. **index 遗漏/失效**：wiki 页面未进 index，或 index 指向已不存在的页面。
4. **无出处论断**：concept/entity 页中事实性段落没有 `[[source]]` 引用。
5. **矛盾**：未解决的「⚠️ 冲突」标记；以及你发现的同一事实在不同页面说法不一致。
6. **frontmatter**：缺必填字段、日期格式错误、`raw` 路径不存在。
7. **项目过期**：`projects/*.md` 中 `status: active` 但 `updated` 超过 14 天。
8. **对齐提示**：`my_level_suggested` 与 `my_level` 不同的页面，列出来让我决定。
9. **可疑内容**：log 中 `[suspicious]` 条目及对应页面。

**先跑 `.venv/Scripts/python -I scripts/lint.py`**：它覆盖 1、2、3、6、7、8、9 这几项机械检查，结果直接采用，不要再逐页人工复查。
LLM 只负责脚本做不了的语义检查：4（无出处论断）和 5（矛盾），以及对脚本结果给出修复建议。
脚本有误报或漏报时，在报告中注明，并建议我修改脚本。

## 输出
- 写到 `digests/YYYY-MM-DD-lint.md`：按检查项分组，每项给出页面与建议动作。
- 追加 log：`[lint] N 个问题`。
- 对话中给我一个 5 行以内的摘要。
