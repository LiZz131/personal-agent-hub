"""主控派活：在项目目录里启动 / 续跑后台工作会话（claude --bg）。

- 正在运行的会话（终端里的交互式会话或后台会话）不用这个脚本：主控直接用 SendMessage 工具发给它
  （名字从 sessions.py 或 ListAgents 拿）。空闲的会话收到消息会自动开始新的一轮。
- 后台会话我随时可以在终端里 `claude attach <名字或短 id>` 接手，两边都能对话。
- 默认直接在项目的工作副本里改（worktree.bgIsolation=none），这样成果就在我平时看的目录里；
  --isolate 则让它进独立的 git worktree（和我同时改同一个仓库时用）。

远程项目（projects/<p>.md 有 remote_outbox）加 --remote：
- 跨会话消息只在同一台机器内有效，所以远程用「转发」：在服务器上起一个临时的无头 Haiku 会话，
  由它用 SendMessage 把消息原样转给目标会话（空闲的会话同样会被唤醒）。一次 SSH + 一次 Haiku。
- ⚠️ 没有 remote_outbox 的项目一律按本机处理；项目页写了访问限制的服务器，绝不在上面运行 claude。

用法:
  worker.py new <项目> "<任务>" [--name 名字] [--model 模型] [--cwd 目录] [--isolate] [--remote]
  worker.py send <会话名字> "<消息>" --remote <项目>    # 只用于远程；本机直接用 SendMessage 工具
  worker.py resume <会话 id> "<消息>" [--isolate] [--remote <项目>]   # 只用于已关闭的会话
  worker.py stop <名字或短 id> [--remote <项目>]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hub_state as hs  # noqa: E402
from sessions import CLAUDE, live_agents  # noqa: E402

ROOT = hs.ROOT


def opt(name: str, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv and sys.argv.index(name) + 1 < len(sys.argv) else default


def positional() -> list[str]:
    out, skip = [], False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a in ("--name", "--model", "--cwd") or (a == "--remote" and _remote_has_value()):
            skip = True
            continue
        if not a.startswith("--"):
            out.append(a)
    return out


def _remote_has_value() -> bool:
    i = sys.argv.index("--remote")
    return i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--") and sys.argv[1] in ("send", "resume", "stop")


def remote_cfg(project: str) -> dict:
    """{ssh, workdir, claude}：ssh 别名来自 remote_outbox，工作目录取 local_paths 里第一个 / 开头的路径。"""
    import yaml

    p = ROOT / "projects" / f"{project}.md"
    if not p.exists():
        raise SystemExit(f"没有项目 {project}（projects/{project}.md 不存在）")
    fm = yaml.safe_load(p.read_text(encoding="utf-8").split("---")[1]) or {}
    r = fm.get("remote_outbox") or {}
    if not r.get("ssh"):
        raise SystemExit(f"{project} 不是远程项目（projects/{project}.md 没有 remote_outbox）")
    workdir = next((str(x) for x in fm.get("local_paths") or [] if str(x).startswith("/")), "~")
    return {"ssh": r["ssh"], "workdir": workdir, "claude": "~/.local/bin/claude"}


def ssh(host: str, remote_cmd: str, stdin: str = "", timeout: int = 180) -> str:
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, remote_cmd],
                       input=stdin.encode("utf-8"), capture_output=True, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    if p.returncode != 0:
        raise SystemExit(f"ssh {host} 失败（exit {p.returncode}）：{out.strip()[-500:]}")
    return out


def remote_bg(cfg: dict, cwd: str, extra: list[str], prompt: str) -> tuple[str, str]:
    import shlex

    cmd = [cfg["claude"], "--bg", "--permission-mode", "auto", *extra]
    if "--isolate" not in sys.argv:
        cmd += ["--settings", json.dumps({"worktree": {"bgIsolation": "none"}})]
    # 提示词经 stdin 传过去，避免 shell 转义问题
    line = f"cd {shlex.quote(cwd)} && " + " ".join(c if c.startswith("~") else shlex.quote(c) for c in cmd) + ' "$(cat)"'
    out = ssh(cfg["ssh"], line, stdin=prompt)
    m = re.search(r"backgrounded\s*·\s*(\S+)\s*·\s*(.+)", out)
    if not m:
        raise SystemExit(f"远程启动失败：{out.strip()[-500:]}")
    return m.group(1).strip(), m.group(2).strip()


FORWARD = """你是一个转发器。用 SendMessage 工具把 <msg> 标签里的内容**原样**发给名为「{name}」的会话（to 就填这个名字），
不要改写、不要增删任何字，也不要做其他任何事。发送成功后只回复 SENT。
如果发送失败或找不到这个会话，先调用 ListAgents，然后回复 NOT-FOUND 加上 ListAgents 的结果。
<msg>
{msg}
</msg>"""


def project_cwd(project: str) -> str:
    import yaml

    p = ROOT / "projects" / f"{project}.md"
    if not p.exists():
        raise SystemExit(f"没有项目 {project}（projects/{project}.md 不存在）；可以用 --cwd 指定目录")
    fm = yaml.safe_load(p.read_text(encoding="utf-8").split("---")[1]) or {}
    paths = fm.get("local_paths") or []
    if not paths:
        raise SystemExit(f"{project} 没有本地目录（可能是远程项目，远程派活在 M4 实现）")
    return str(paths[0])


def run_bg(cwd: str, extra: list[str], prompt: str) -> tuple[str, str]:
    cmd = [CLAUDE, "--bg", "--permission-mode", "auto", *extra]
    if "--isolate" not in sys.argv:
        cmd += ["--settings", json.dumps({"worktree": {"bgIsolation": "none"}})]
    cmd.append(prompt)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=90,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    m = re.search(r"backgrounded\s*·\s*(\S+)\s*·\s*(.+)", out)
    if p.returncode != 0 or not m:
        raise SystemExit(f"启动失败（exit {p.returncode}）：{out.strip()[-500:]}")
    note = "\n".join(ln for ln in out.splitlines() if ln.strip().lower().startswith("note"))
    return m.group(1).strip(), m.group(2).strip() + (f"\n{note}" if note else "")


def log_state(project: str, line: str) -> None:
    st = hs.load_state(project)
    st["sections"]["记录"].append(f"- {hs.stamp()} {line}")
    hs.save_state(project, st)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pos = positional()
    if not pos:
        sys.exit(__doc__)
    cmd = pos[0]
    remote = opt("--remote") if "--remote" in sys.argv and _remote_has_value() else None
    if cmd == "new" and len(pos) >= 3:
        project, task = pos[1], pos[2]
        name = opt("--name") or f"hub-{project}-{time.strftime('%m%d-%H%M')}"
        extra = ["-n", name] + (["--model", opt("--model")] if opt("--model") else [])
        if "--remote" in sys.argv:
            cfg = remote_cfg(project)
            cwd = opt("--cwd") or cfg["workdir"]
            bg_id, label = remote_bg(cfg, cwd, extra, task)
            log_state(project, f"主控派活 → {cfg['ssh']} 后台会话 {name}（{bg_id}）：{task[:120]}")
            print(f"已在 {cfg['ssh']} 启动后台会话 {label}（短 id {bg_id}），目录 {cwd}\n"
                  f"- 给它发消息：worker.py send {name} \"…\" --remote {project}\n"
                  f"- 我在终端接手：ssh {cfg['ssh']} 后运行 claude attach {bg_id}")
            return
        cwd = opt("--cwd") or project_cwd(project)
        bg_id, label = run_bg(cwd, extra, task)
        log_state(project, f"主控派活 → 后台会话 {name}（{bg_id}）：{task[:120]}")
        print(f"已启动后台会话 {label}（短 id {bg_id}），目录 {cwd}\n"
              f"- 给它发消息：SendMessage to=\"{name}\"\n- 我在终端接手：claude attach {bg_id}")
    elif cmd == "send" and len(pos) >= 3 and remote:
        cfg = remote_cfg(remote)
        out = ssh(cfg["ssh"], f"cd ~ && {cfg['claude']} -p --model haiku --strict-mcp-config --no-session-persistence "
                              "--allowedTools SendMessage ListAgents", stdin=FORWARD.format(name=pos[1], msg=pos[2]))
        ok = "SENT" in out and "NOT-FOUND" not in out
        log_state(remote, f"主控转达 → {cfg['ssh']} 会话 {pos[1]}：{pos[2][:120]}" + ("" if ok else "（失败）"))
        print(f"已送达 {cfg['ssh']} 上的 {pos[1]}" if ok else f"转达失败：{out.strip()[-800:]}")
    elif cmd == "resume" and len(pos) >= 3 and remote:
        cfg = remote_cfg(remote)
        reg = next((r for r in hs.sessions(include_closed=True) if r["session_id"].startswith(pos[1])), {})
        bg_id, label = remote_bg(cfg, opt("--cwd") or reg.get("cwd") or cfg["workdir"], ["--resume", reg.get("session_id", pos[1])], pos[2])
        log_state(remote, f"主控续跑 {cfg['ssh']} 会话 {pos[1][:8]} → 后台 {bg_id}：{pos[2][:120]}")
        print(f"已在 {cfg['ssh']} 后台续跑：{label}（短 id {bg_id}）")
    elif cmd == "stop" and len(pos) >= 2 and remote:
        cfg = remote_cfg(remote)
        print(ssh(cfg["ssh"], f"{cfg['claude']} stop {pos[1]}").strip())
    elif cmd == "resume" and len(pos) >= 3:
        sid, msg = pos[1], pos[2]
        live = [a for a in live_agents() if a.get("sessionId", "").startswith(sid)]
        if live:
            raise SystemExit(f"会话正在运行（{live[0].get('name')}），不要 resume：直接用 SendMessage 发给它")
        reg = next((r for r in hs.sessions(include_closed=True) if r["session_id"].startswith(sid)), None)
        if not reg:
            raise SystemExit(f"登记表里找不到会话 {sid}；可以用 sessions.py 查")
        bg_id, label = run_bg(opt("--cwd") or reg["cwd"], ["--resume", reg["session_id"]], msg)
        log_state(reg.get("project", ""), f"主控续跑会话 {reg['session_id'][:8]} → 后台 {bg_id}：{msg[:120]}")
        print(f"已在后台续跑：{label}（短 id {bg_id}）\n- 我在终端接手：claude attach {bg_id}")
    elif cmd == "stop" and len(pos) >= 2:
        p = subprocess.run([CLAUDE, "stop", pos[1]], capture_output=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print((p.stdout + p.stderr).decode("utf-8", "replace").strip())
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
