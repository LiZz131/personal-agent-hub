"""从远程服务器的发件箱拉取 Claude Code 事件，交给 hub_notify 记录并推送。

远程 hook（cc_hook.py --outbox …）只往 ~/.hub-outbox/*.json 写文件，远程不存放任何飞书凭证。
本脚本由任务计划程序每分钟运行一次；每台服务器每次只建立**一次** SSH 连接（只读文件 + 移到 sent/）。
服务器列表来自 projects/*.md frontmatter 的 remote_outbox: {ssh: <主机别名>, dir: <发件箱>}。

用法: .venv\\Scripts\\python -I scripts\\pull_remote_events.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hub_notify import ROOT, deliver  # noqa: E402

# 读出所有事件后移到 sent/（保留 7 天便于排查）；只匹配 *.json，正在写入的 .tmp 不会被读到
DRAIN = ('cd {dir} 2>/dev/null || exit 0; mkdir -p sent; '
         'for f in *.json; do [ -e "$f" ] || continue; cat "$f"; echo; mv "$f" sent/; done; '
         'find sent -type f -mtime +7 -delete 2>/dev/null; true')


def remotes() -> list[dict]:
    import yaml

    out = []
    for p in sorted((ROOT / "projects").glob("*.md")):
        try:
            fm = yaml.safe_load(p.read_text(encoding="utf-8").split("---")[1]) or {}
        except Exception:  # noqa: BLE001
            continue
        r = fm.get("remote_outbox")
        if isinstance(r, dict) and r.get("ssh"):
            out.append({"project": p.stem, "ssh": r["ssh"], "dir": r.get("dir", "~/.hub-outbox")})
    return out


def pull(remote: dict) -> list[dict]:
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", remote["ssh"], DRAIN.format(dir=remote["dir"])]
    p = subprocess.run(cmd, capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0:
        raise RuntimeError(f"ssh {remote['ssh']} exit {p.returncode}: {p.stderr.decode('utf-8', 'replace')[-300:]}")
    events = []
    for line in p.stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    return events


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    total = 0
    for r in remotes():
        try:
            for ev in pull(r):
                ev.setdefault("project", r["project"])
                ev.setdefault("host", r["ssh"])
                deliver(ev)
                total += 1
        except Exception as e:  # noqa: BLE001  一台服务器出错不影响其他服务器
            (ROOT / "events").mkdir(exist_ok=True)
            with open(ROOT / "events" / "pull_errors.log", "a", encoding="utf-8") as f:
                import time
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {r['ssh']}: {e}\n")
    if total:
        print(f"delivered {total} remote event(s)")


if __name__ == "__main__":
    main()
