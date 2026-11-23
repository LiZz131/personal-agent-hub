---
name: calibrate
description: 给 wiki 中的概念/实体页定级（my_level），让讲解深度与我对齐。Use when the user says 定级/校准/calibrate, or 想处理 lint 报告里的待定级列表.
---

# Calibrate

1. 运行 `.venv/Scripts/python -I scripts/lint.py --json`，取 `calibration` 列表（`my_level` 为 unknown，或与 `my_level_suggested` 不一致的页面）。列表为空就告诉我"无需定级"并结束。
2. 每页读一下 frontmatter 和「定义」段，用一句话概括这个概念，方便我判断。
3. 用 AskUserQuestion 提问，每次最多 4 页（每页一个问题）：
   - 问题：`<页面名>：<一句话概括>。你对它的掌握程度？`
   - 选项：新手 / 会用 / 熟练 / 精通。有 `my_level_suggested` 的，把建议值放第一个并标注"(建议)"。
   - 我选 Other 并写"跳过"的，保持 unknown。
4. 按我的回答改 `my_level`。若和 `my_level_suggested` 一致，清空 suggested；更新 `updated`。我在 Other 里附带的说明，原样追加到 `## My notes`（格式 `- YYYY-MM-DD <原话>`）。
5. 还有剩余页面就继续下一批，直到列表处理完或我说停。
6. 追加 log：`- YYYY-MM-DD HH:MM [calibrate] <page>=<level>, ...`。

不要根据我的使用者档案（`config/hub.yaml` 的 `memory_dir`）自行推断并直接写入 `my_level`。推断结果只能作为"(建议)"选项给我选。
