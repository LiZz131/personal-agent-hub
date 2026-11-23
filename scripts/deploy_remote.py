"""把 hub 的远程组件部署到远程服务器（projects/*.md 里有 remote_outbox 的项目）。

部署内容（都只用标准库，不放任何凭证）：
  ~/.hub/bin/cc_hook.py         Notification / Stop hook → 发件箱
  ~/.hub/bin/checkpoint.py      远程检查点提交
  ~/.claude/skills/checkpoint/SKILL.md   远程版 checkpoint skill（命令换成远程路径）
不改服务器上的 ~/.claude/settings.json（hook 的注册已在 4b 完成）。

用法: .venv\\Scripts\\python -I scripts\\deploy_remote.py [项目]
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hub_notify import ROOT  # noqa: E402
from pull_remote_events import remotes  # noqa: E402

LOCAL_SKILL = Path.home() / ".claude" / "skills" / "checkpoint" / "SKILL.md"
LOCAL_CMD = f'{ROOT / ".venv" / "Scripts" / "python.exe"} -I {ROOT / "scripts" / "checkpoint.py"}'
REMOTE_CMD = "python3 ~/.hub/bin/checkpoint.py"


def run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, timeout=120)
    if p.returncode != 0:
        raise SystemExit(f"{' '.join(cmd[:3])} … 失败：{(p.stdout + p.stderr).decode('utf-8', 'replace')[-400:]}")


def deploy(r: dict) -> None:
    host_label = r["ssh"].split("-cloudlab")[0]
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "cc_hook.py").write_bytes((ROOT / "scripts" / "cc_hook.py").read_bytes())
        cp = (ROOT / "scripts" / "remote" / "checkpoint_remote.py").read_text(encoding="utf-8")
        (td / "checkpoint.py").write_text(cp.replace("__PROJECT__", r["project"]).replace("__HOST__", host_label),
                                          encoding="utf-8", newline="\n")
        skill = LOCAL_SKILL.read_text(encoding="utf-8").replace(LOCAL_CMD, REMOTE_CMD)
        skill = skill.replace("（路径固定，不依赖当前项目的 Python 环境）", "（服务器上的 hub 组件，只用标准库）")
        (td / "SKILL.md").write_text(skill, encoding="utf-8", newline="\n")
        run(["ssh", "-o", "BatchMode=yes", r["ssh"], "mkdir -p ~/.hub/bin ~/.claude/skills/checkpoint"])
        run(["scp", "-q", "-o", "BatchMode=yes", str(td / "cc_hook.py"), str(td / "checkpoint.py"), f"{r['ssh']}:.hub/bin/"])
        run(["scp", "-q", "-o", "BatchMode=yes", str(td / "SKILL.md"), f"{r['ssh']}:.claude/skills/checkpoint/SKILL.md"])
    print(f"已部署到 {r['ssh']}（项目 {r['project']}）")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for r in remotes():
        if not only or r["project"] == only:
            deploy(r)


if __name__ == "__main__":
    main()
