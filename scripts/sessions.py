"""列出所有工作会话：本机正在运行的（claude agents --json）+ 会话登记表里最近的，按项目分组。

主控用它回答「X 现在有哪些会话、在干什么」，并拿到给会话发消息用的名字（SendMessage 的 to）。

--remote 时再通过 SSH 列出远程服务器（projects/*.md 的 remote_outbox）上正在运行的会话。

用法: .venv\\Scripts\\python -I scripts\\sessions.py [项目] [--days N] [--remote] [--all] [--json]
      .venv\\Scripts\\python -I scripts\\sessions.py ignore <会话 id> [备注]   # 我说「忽略这个会话」时用
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hub_config as hc  # noqa: E402
import hub_state as hs  # noqa: E402
from hub_notify import project_for  # noqa: E402

CLAUDE = hc.claude_bin()


def live_agents() -> list[dict]:
    """本机正在运行的会话（交互式 + 后台）。字段：name、kind、status/state、waitingFor、sessionId、cwd、id（后台短 id）。"""
    try:
        p = subprocess.run([CLAUDE, "agents", "--json"], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return json.loads(p.stdout.decode("utf-8", "replace") or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def remote_rows(project: str | None = None) -> list[dict]:
    """远程服务器上正在运行的会话（每台一次 SSH：claude agents --json）。"""
    from pull_remote_events import remotes

    rows = []
    for r in remotes():
        if project and r["project"] != project:
            continue
        try:
            p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", r["ssh"],
                                "~/.local/bin/claude agents --json"], capture_output=True, timeout=60,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            agents = json.loads(p.stdout.decode("utf-8", "replace") or "[]")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            rows.append({"session_id": "", "project": r["project"], "live": False, "state": f"⚠ 连不上 {r['ssh']}",
                         "cwd": "", "title": "", "updated": time.time()})
            continue
        for a in agents:
            rows.append({"session_id": a.get("sessionId", ""), "project": r["project"], "live": True,
                         "name": a.get("name", ""), "kind": f"{a.get('kind', '')} @{r['ssh']}", "bg_id": a.get("id", ""),
                         "cwd": a.get("cwd", ""), "state": a.get("state") or a.get("status", ""),
                         "waiting_for": a.get("waitingFor", ""), "title": "", "updated": time.time()})
    return rows


def collect(project: str | None = None, days: float = 3, remote: bool = False) -> list[dict]:
    rows = {}
    for r in hs.sessions(include_closed=True):
        if time.time() - r.get("updated", 0) > days * 86400:
            continue
        rows[r["session_id"]] = {"session_id": r["session_id"], "project": r.get("project", ""),
                                 "driver": r.get("driver", ""),
                                 "cwd": r.get("cwd", ""), "title": r.get("title", ""), "live": False,
                                 "state": r.get("state", ""), "updated": r.get("updated", 0)}
    for a in live_agents():
        sid = a.get("sessionId", "")
        row = rows.setdefault(sid, {"session_id": sid, "title": "", "updated": time.time()})
        row.update(live=True, name=a.get("name", ""), kind=a.get("kind", ""), bg_id=a.get("id", ""),
                   cwd=a.get("cwd", row.get("cwd", "")),
                   state=a.get("state") or a.get("status") or row.get("state", ""),
                   waiting_for=a.get("waitingFor", ""))
        row["project"] = "主控（飞书）" if row.get("driver") == "飞书主控" else project_for(row["cwd"])
    if remote:
        for r in remote_rows(project):
            rows[r["session_id"] or f"remote-{len(rows)}"] = r
    out = [r for r in rows.values() if (not project or r.get("project") == project)
           and ("--all" in sys.argv or not hs.is_ignored(r.get("session_id", "")))]
    return sorted(out, key=lambda r: (r.get("project", ""), not r["live"], -r.get("updated", 0)))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    days = float(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 3
    if "--days" in sys.argv:
        args = [a for a in args if a != sys.argv[sys.argv.index("--days") + 1]]
    rows = collect(args[0] if args else None, days, remote="--remote" in sys.argv)
    if args[:1] == ["ignore"] and len(args) >= 2:   # sessions.py ignore <会话 id> [备注]
        hs.ignore_session(args[1], " ".join(args[2:]))
        print(f"已忽略会话 {args[1]}（state/ignored_sessions.txt；sessions.py --all 仍可看到）")
        return
    if "--json" in sys.argv:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    cur = None
    for r in rows:
        if r.get("project") != cur:
            cur = r.get("project")
            print(f"\n## {cur}")
        when = time.strftime("%m-%d %H:%M", time.localtime(r.get("updated", 0)))
        state = r.get("state", "") + (f"（{r['waiting_for']}）" if r.get("waiting_for") else "")
        if r["live"]:
            who = f"🟢 {r.get('name', '')}  [{r.get('kind', '')}{' ' + r['bg_id'] if r.get('bg_id') else ''}]"
        else:
            who = "⚪ 已关闭"
        print(f"- {who} · {state} · 「{r.get('title', '')}」 · {r['cwd']} · 会话 {r['session_id']} · {when}")
    if not rows:
        print("（没有会话）")


if __name__ == "__main__":
    main()
