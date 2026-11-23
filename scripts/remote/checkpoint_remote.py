#!/usr/bin/env python3
"""远程服务器上的检查点提交（部署到 ~/.hub/bin/checkpoint.py；只用标准库，服务器上不放任何凭证）。

把检查点写进发件箱 ~/.hub-outbox/，由 hub 的事件流（SSH 长连接）取回本机；
本机再用 scp 取回产物文件，发到飞书。用法与本机的 scripts/checkpoint.py 相同：
  python3 ~/.hub/bin/checkpoint.py --title … --question … [--summary …] [--artifact 文件 …] [--notion …]
项目名、主机名由部署时写在下面的常量决定（deploy 时替换）。
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

PROJECT = "__PROJECT__"
HOST = "__HOST__"
OUTBOX = Path("~/.hub-outbox").expanduser()


def main():
    ap = argparse.ArgumentParser(description="提交检查点")
    ap.add_argument("--title", required=True)
    ap.add_argument("--question", required=True)
    ap.add_argument("--summary", default="")
    ap.add_argument("--artifact", action="append", default=[])
    ap.add_argument("--notion", default="")
    a = ap.parse_args()
    arts = []
    for x in a.artifact:
        p = Path(x).expanduser()
        p = p if p.is_absolute() else Path.cwd() / p
        if not p.exists():
            sys.exit(f"产物不存在：{p}")
        arts.append(str(p.resolve()))
    ev = {"id": uuid.uuid4().hex[:12], "ts": time.time(), "kind": "checkpoint", "project": PROJECT, "host": HOST,
          "cwd": os.getcwd(), "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID", ""), "source": "remote-checkpoint",
          "title": a.title, "question": a.question, "summary": a.summary, "artifacts": arts, "notion": a.notion,
          "message": f"{a.title}：{a.question}"}
    OUTBOX.mkdir(parents=True, exist_ok=True)
    tmp = OUTBOX / f".{ev['id']}.tmp"
    tmp.write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
    tmp.rename(OUTBOX / f"{ev['id']}.json")
    print(f"已提交检查点「{a.title}」，会发到飞书等用户决定。\n"
          "现在请结束本轮：不要继续依赖这个决定的工作。用户的决定会由主控以跨会话消息送回本会话。")


if __name__ == "__main__":
    main()
