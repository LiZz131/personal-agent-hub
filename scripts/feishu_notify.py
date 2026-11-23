"""把一段 Markdown 推送给本人（及 FEISHU_DIGEST_CHAT_IDS）。

用法: .venv\\Scripts\\python -I scripts/feishu_notify.py --title "标题" --file path.md
      echo 内容 | .venv\\Scripts\\python -I scripts/feishu_notify.py --title "标题"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feishu_common import load_env, make_client, notify_owner  # noqa: E402

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default=None)
    ap.add_argument("--file", default=None)
    a = ap.parse_args()
    if a.file:
        raw = Path(a.file).read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
    else:
        text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    text = text.strip() or "（无输出）"
    env = load_env()
    notify_owner(env, make_client(env), text, a.title)
