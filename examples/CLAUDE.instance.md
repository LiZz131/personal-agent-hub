# 本实例的约定（CLAUDE.instance.md）/ Instance settings

框架约定在 `CLAUDE.md`；这里只放这个实例（你自己）的个人设置和项目特例，由 `CLAUDE.md` 导入。两者冲突时以本文件为准。
The framework contract lives in `CLAUDE.md`, which imports this file. Put your personal settings and
per-project exceptions here; this file wins on conflict. `scripts/install.ps1` copies it to the repo root if missing.

## 使用者 / User

- 背景与偏好见 `config/hub.yaml` 的 `memory_dir`（例如 `identity.md` / `principles.md` 必读，其余按需）。只读，不复制到本仓库。没有就删掉这一行。
  Background notes: the `memory_dir` folder in `config/hub.yaml` (read-only). Delete this line if you don't have one.
- 中文交流，术语/代码/文献保留英文；直接给结论 + 理由。
  (English alternative: "Reply in English; give the conclusion first, then the reasoning.")

## 项目特例 / Project exceptions

<!-- 每个项目的访问限制和特殊约定写在这里，agent 会严格遵守。例如：
     Access restrictions per project; agents follow them strictly. For example: -->
- 远程项目 `example-project`：服务器 `gpu-box` 上的 hook 写 `~/.hub-outbox/`，由 `scripts/remote_event_stream.py` 推回本机；会话记录通过只读 SSH 查看。
- ⚠️ 某台服务器（例如 `cluster-login`）有跳板机：**禁止直接 ssh**，也绝不在上面运行 claude；必须用该项目工作区里的专用工具。
