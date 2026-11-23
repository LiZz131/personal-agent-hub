"""install.ps1 的辅助：合并用户级 hook、按 {{HUB}} 渲染模板。只用标准库（首次安装时依赖可能还没装）。
Helpers for scripts/install.ps1: merge user-level hooks, render {{HUB}} templates. Stdlib only.

    python install_util.py hooks  --settings <~/.claude/settings.json> --hub <hub> [--dry-run]
    python install_util.py render --src <模板> --dst <输出> --hub <hub> [--slash] [--force] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

EVENTS = ["SessionStart", "UserPromptSubmit", "Notification", "Stop", "SessionEnd"]


def hook_entry(hub: Path) -> dict:
    return {"type": "command",
            "command": str(hub / ".venv" / "Scripts" / "python.exe"),
            "args": ["-I", str(hub / "scripts" / "cc_hook.py")],
            "timeout": 10}


def is_hub_hook(h: dict) -> bool:
    text = " ".join([str(h.get("command", ""))] + [str(a) for a in h.get("args") or []])
    return "cc_hook.py" in text


def backup(p: Path) -> Path:
    b = p.with_name(f"{p.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(p, b)
    return b


def merge_hooks(settings: Path, hub: Path, dry: bool) -> int:
    data = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    if not isinstance(data, dict):
        raise SystemExit(f"{settings} 不是 JSON 对象 / not a JSON object")
    hooks = data.setdefault("hooks", {})
    want = hook_entry(hub)
    changes = []
    for ev in EVENTS:
        groups = hooks.setdefault(ev, [])
        found = False
        for g in groups:
            for i, h in enumerate(g.get("hooks") or []):
                if isinstance(h, dict) and is_hub_hook(h):
                    found = True
                    if h != want:
                        g["hooks"][i] = want
                        changes.append(f"{ev}: 更新 / update")
        if not found:
            groups.append({"hooks": [want]})
            changes.append(f"{ev}: 新增 / add")
    if not changes:
        print(f"  hooks 已是最新 / up to date: {settings}")
        return 0
    for c in changes:
        print(f"  {c}")
    if dry:
        print(f"  [dry-run] 不写入 / not writing {settings}")
        return 0
    settings.parent.mkdir(parents=True, exist_ok=True)
    if settings.exists():
        print(f"  备份 / backup -> {backup(settings)}")
    settings.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  已写入 / written: {settings}")
    return 0


def render(src: Path, dst: Path, hub: Path, slash: bool, force: bool, dry: bool) -> int:
    hub_s = hub.as_posix() if slash else str(hub)
    out = src.read_text(encoding="utf-8").replace("{{HUB}}", hub_s)
    if dst.exists():
        if dst.read_text(encoding="utf-8") == out:
            print(f"  已是最新 / up to date: {dst}")
            return 0
        if not force:
            print(f"  已存在，跳过 / exists, skipped: {dst}")
            return 0
    if dry:
        print(f"  [dry-run] 将写入 / would write {dst}")
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        print(f"  备份 / backup -> {backup(dst)}")
    dst.write_text(out, encoding="utf-8", newline="\n")
    print(f"  已写入 / written: {dst}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hooks")
    h.add_argument("--settings", required=True)
    h.add_argument("--hub", required=True)
    h.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("render")
    r.add_argument("--src", required=True)
    r.add_argument("--dst", required=True)
    r.add_argument("--hub", required=True)
    r.add_argument("--slash", action="store_true", help="用正斜杠路径 / forward slashes (TOML)")
    r.add_argument("--force", action="store_true", help="内容不同时覆盖（先备份） / overwrite with backup")
    r.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    hub = Path(a.hub).resolve()
    if a.cmd == "hooks":
        return merge_hooks(Path(a.settings), hub, a.dry_run)
    return render(Path(a.src), Path(a.dst), hub, a.slash, a.force, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
