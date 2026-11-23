"""工作 agent 提交检查点：阶段成果需要我验收，或者需要我做方向性决定。

检查点不经过分诊判断，直接发一张提醒卡片（带 #<项目>-<序号> 标签），产物文件作为附件一起发到飞书；
记进 state/<项目>.md 的「待我决定」。我的决定由主控转达回提交它的会话（跨会话消息）。
用户级 skill `checkpoint`（~/.claude/skills/checkpoint/）说明何时、如何调用。

用法（在任何项目目录里）：
  <hub>\\.venv\\Scripts\\python.exe -I <hub>\\scripts\\checkpoint.py \\
      --title "剧本 v2 待审" --question "要我决定 / 验收什么" --summary "做了什么、关键结果" \\
      [--artifact <文件> ...] [--notion "建议的 Notion 变更"] [--project <项目>]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="提交检查点")
    ap.add_argument("--title", required=True)
    ap.add_argument("--question", required=True)
    ap.add_argument("--summary", default="")
    ap.add_argument("--artifact", action="append", default=[])
    ap.add_argument("--notion", default="")
    ap.add_argument("--project", default="")
    a = ap.parse_args()

    from hub_notify import deliver, new_event, project_for

    cwd = os.getcwd()
    arts = []
    for x in a.artifact:
        p = Path(x)
        p = p if p.is_absolute() else Path(cwd) / p
        if not p.exists():
            sys.exit(f"产物不存在：{p}")
        arts.append(str(p.resolve()))
    ev = new_event("checkpoint", project=a.project or project_for(cwd), cwd=cwd,
                   session_id=os.environ.get("CLAUDE_CODE_SESSION_ID", ""), source="checkpoint",
                   title=a.title, question=a.question, summary=a.summary, artifacts=arts, notion=a.notion,
                   message=f"{a.title}：{a.question}")
    deliver(ev)
    print(f"已提交检查点「{a.title}」（项目 {ev['project']}），会发到飞书等用户决定。\n"
          "现在请结束本轮：不要继续依赖这个决定的工作。用户的决定会由主控以跨会话消息送回本会话。")


if __name__ == "__main__":
    main()
