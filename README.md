# Personal Agent Hub

[中文说明](README.zh-CN.md)

A personal knowledge base and project supervisor built on top of [Claude Code](https://code.claude.com/). It is a folder of conventions (`CLAUDE.md`, skills, roles, subagents) plus a set of Python/PowerShell scripts. There is no server of its own: Claude Code does the reasoning, the scripts do the bookkeeping, and Feishu (Lark) is the phone-side UI.

It does two things:

1. **LLM wiki** (after Karpathy's "LLM Wiki" idea: raw → wiki → schema). You drop links, PDFs or notes into `inbox/`; agents save the source under `raw/`, write a source page, update concept/entity pages, an index and a log. Concept pages carry your self-assessed level (`my_level`) so explanations are pitched at the right depth. On top of that: a daily feed digest scored against `interests.md`, a learning queue, and a tutor role.
2. **Supervisor over Claude Code sessions.** Every interactive or background Claude Code session on your machine (and optionally on remote servers) reports its events through user-level hooks. A cheap headless model (Haiku) triages each event: stay silent, append a line to the project's daily status card, or notify you. You talk to one "supervisor" session in Feishu; it relays your decisions to the right working session via Claude Code's cross-session messages, starts or resumes background sessions, and keeps its memory in files rather than in its context.

Built and used daily by one person on Windows. Expect rough edges; see [Limitations](#limitations).

## Features

- **Triage instead of raw notifications.** Hook events → `events/log.jsonl` → `scripts/triage.py` (Haiku) → silent / append to the daily status card (updated in place, no ping) / new message tagged `#<project>-<n>`. If triage fails, the raw notification is sent so nothing is lost. Your feedback ("don't tell me about this kind of thing") is appended to the project's `## 分诊规则` (triage rules) section and read first next time.
- **Checkpoints.** Working agents call the user-level `checkpoint` skill when they need sign-off or a direction decision. You get a card with the question, a summary and the artifacts attached; reply "通过" (approve) or "打回 + comments" (reject) and the supervisor relays it back to the session.
- **Daily brief** (`daily-brief` skill): pending decisions per project, last 24h of activity, blocked sessions, and (optionally) due tasks from a Notion task database.
- **Experiment records** (`exp-record` skill, `scripts/exp_draft.py`): when triage sees an experiment stage conclude, a draft record is assembled from project state, session transcripts and git history, sent as a checkpoint, and written to a Notion database only after you approve.
- **Auto-ops incidents** (`scripts/incident.py`, `ops-fix` skill): when the hub's own scripts fail, an incident file is opened under `ops/incidents/` (deduplicated by fingerprint), handed to a running dev session or a background fixer session, and closed with root cause / fix / verification reported verbatim. Fixes are committed locally and pushed only after you approve.
- **Remote projects** (optional): servers run the same hook and write to `~/.hub-outbox/`; `scripts/remote_event_stream.py` keeps one SSH connection per server and streams events back. No hub secrets are stored on servers.
- **Wiki tooling**: `ingest`, `query`, `lint`, `calibrate`, `digest` skills; four roles (librarian, Q&A, planner, tutor); `reader` / `clerk` / `scout` subagents pinned to cheaper models to save quota.

## Architecture

```
  Feishu (phone / desktop)
      │  chat, cards, files
      ▼
  cc-connect (hub-branch fork) ──► supervisor session: Claude Code in <hub>, reads CLAUDE.md
      │                                  │  SendMessage / worker.py / sessions.py
      │                                  ▼
      │                         working sessions: Claude Code in your project dirs
      │                         (terminal or `claude --bg`; local, or remote over SSH)
      │                                  │  user-level hooks: SessionStart, UserPromptSubmit,
      │                                  │  Notification, Stop, SessionEnd → scripts/cc_hook.py
      │                                  ▼
      │                         events/log.jsonl, events/sessions/*.json
      │                                  │
      │                                  ▼
      └──── Feishu API ◄──────── scripts/triage.py (headless Haiku)
            (status cards,               │ writes
             notifications,              ▼
             checkpoints)        state/<project>.md  (pending decisions, today's activity)
                                 projects/<project>.md (your project page + triage rules)

  Scheduled (Windows Task Scheduler / cc-connect cron): inbox ingest, weekly lint,
  study plan, feed digest, daily brief.        Optional: Notion via the official Notion MCP.
```

## Requirements

- **Windows 10/11** (v1 is Windows-only: PowerShell scripts, Task Scheduler, `pythonw` tricks).
- **Claude Code CLI** (`claude`), logged in. Background sessions (`claude --bg`), `claude agents` and cross-session messaging are used.
- **[uv](https://docs.astral.sh/uv/)** for the Python environment (`.venv` inside the repo; Python 3.12 tested).
- **git**.
- **A Feishu (or Lark) self-built app** with bot capability: see `docs/feishu-setup.md`.
- **cc-connect, hub branch of the fork** <https://github.com/LiZz131/cc-connect> (based on upstream [chenhg5/cc-connect](https://github.com/chenhg5/cc-connect) v1.5.3). Build it from source with Go (`scripts/build-cc-connect.ps1`); no binary is shipped in this repo. See `docs/cc-connect-setup.md`.
- Optional: **Notion** via the official Notion MCP (`.mcp.json`), for the daily brief and experiment records.
- Optional: **SSH** access to remote servers for remote projects.

## Install

```powershell
git clone <this repo> C:\path\to\hub
cd C:\path\to\hub
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -DryRun   # see what it would do
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

`install.ps1` is idempotent. It:

1. copies `config/hub.example.yaml` → `config/hub.yaml`, `.env.example` → `.env`, and `examples/CLAUDE.instance.md`, `examples/interests.md`, `examples/subscriptions.yaml` to the repo root, each only if missing;
2. runs `scripts/setup-venv.ps1` (creates `.venv` with uv, installs `requirements.txt`);
3. merges five hooks into `~/.claude/settings.json` (backup first; an existing `cc_hook.py` entry is updated in place, other hooks are left alone);
4. renders the user-level skill `~/.claude/skills/checkpoint/SKILL.md` from `user-skills/checkpoint/`;
5. renders `tools/cc-connect/config.toml` from `config.toml.example` if missing.

Options: `-UserClaudeDir <dir>` (install into another directory, e.g. for testing), `-Python <python.exe>`, `-SkipVenv`, `-DryRun` / `-WhatIf`.

Then:

1. Fill in `.env` (Feishu app id/secret, your open_id, notification chat ids) following `docs/feishu-setup.md` and `docs/feishu-project-notify.md`.
2. Edit `config/hub.yaml` and `CLAUDE.instance.md` (your language, your background notes folder, per-project restrictions).
3. Build cc-connect and test it in the foreground (`docs/cc-connect-setup.md`).
4. Register scheduled tasks: `scripts\register-tasks.ps1` (cc-connect at logon, remote event stream, inbox ingest, weekly lint, study plan). The feed digest and the 08:00 daily brief run as cc-connect cron jobs (new session per run) so you can ask follow-up questions in Feishu; create them with cc-connect's cron command pointing at the `digest` / `daily-brief` skills.
5. Add a page per project under `projects/` (copy `examples/projects/example-project.md`). For remote projects run `scripts/deploy_remote.py` once.

## Configuration

| File | What | In git? |
|---|---|---|
| `CLAUDE.md` | Framework contract every agent follows | yes (framework) |
| `CLAUDE.instance.md` | Your personal settings and per-project exceptions; imported by `CLAUDE.md`, wins on conflict | your choice |
| `config/hub.yaml` | `language`, `memory_dir` (optional read-only notes about you), `claude_bin`, models for triage / relay / experiment drafts, Notion data source ids, cc-connect path. Read by `scripts/hub_config.py`; missing keys fall back to defaults | your choice |
| `.env` | Secrets only (Feishu app credentials, chat ids, webhook). Denied to Claude Code by `.claude/settings.json` | **never** |
| `interests.md`, `subscriptions.yaml` | Interest profile and feed sources for the digest | your choice |
| `tools/cc-connect/config.toml` | cc-connect config rendered from the template; secrets referenced as `${VAR}` | your choice |

## Directory contract

What the framework reads and writes in your instance:

| Path | Contents | Written by |
|---|---|---|
| `projects/<p>.md` | One page per project. Frontmatter: `local_paths` (session cwd → project), optional `remote_outbox: {ssh, dir}`, optional `notion_projects`. Sections incl. `## 分诊规则` (triage rules) | you / agents with your confirmation |
| `state/<p>.md` | Supervisor memory: pending decisions, working sessions, today's activity, records | triage + supervisor (not in git) |
| `events/` | `log.jsonl`, `sessions/*.json` registry, `cards/` | hooks, triage (not in git) |
| `ops/incidents/`, `ops/INCIDENTS.md` | Incident tickets and generated index | `incident.py`, fixer sessions |
| `drafts/experiments/` | Experiment record drafts | `exp_draft.py` |
| `inbox/`, `raw/`, `wiki/`, `digests/`, `learning/`, `feeds/` | Wiki pipeline (see `CLAUDE.md` §1) | you, ingest, digest, roles |

Project names are file names: `projects/foo.md` ↔ `state/foo.md` ↔ tags `#foo-<n>`. The hub's own incidents are filed under the project name `Personal_Agent`.

## Limitations

- **Windows only.** Paths, PowerShell, Task Scheduler and `.venv\Scripts` are assumed throughout.
- **Feishu only.** Notifications go through the Feishu Open API and cc-connect; there is no notifier abstraction yet.
- **Chinese-first.** `CLAUDE.md`, skills, triage prompts and generated cards are written in Chinese. The `language` key in `config/hub.yaml` is reserved but not used by any script yet. You can tell the agents to reply in English in `CLAUDE.instance.md`; cards and prompts stay Chinese.
- **Depends on recent Claude Code features** (background sessions, `claude agents --json`, cross-session messages, hooks). Behaviour may change between Claude Code releases.
- **Depends on a cc-connect fork.** Upstream cc-connect may work partially.
- **Single user.** One owner open_id; no multi-user access control beyond cc-connect's `allow_from`.
- **Remote projects** assume key-based SSH with a host alias and Claude Code installed on the server.
- **Optional pieces not covered by tests**: Notion integration, remote projects.
- There is no automated test suite; the scripts are verified by use.

## License

Apache License 2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
