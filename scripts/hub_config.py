"""Hub 实例配置：config/hub.yaml（框架只带 config/hub.example.yaml）。

框架脚本里所有与个人实例相关的值（claude 可执行文件、使用者档案目录、Notion 库 id、
各任务用的模型）都从这里取；文件不存在或字段缺失时用默认值。

    .venv/Scripts/python -I scripts/hub_config.py            # 打印合并后的完整配置
    .venv/Scripts/python -I scripts/hub_config.py notion.tasks  # 打印单个字段
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "hub.yaml"

DEFAULTS: dict = {
    "language": "zh",
    "memory_dir": None,
    "claude_bin": "~/.local/bin/claude.exe",
    "models": {"triage": "haiku", "relay": "haiku", "exp_draft": "sonnet"},
    "notion": {"tasks": None, "experiments": None, "projects": None},
    "cc_connect": {"exe": "tools/cc-connect/cc-connect.exe"},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(base.get(k), dict) and isinstance(v, dict) else v
    return out


@lru_cache(maxsize=1)
def load() -> dict:
    data = {}
    if CONFIG.exists():
        data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    return _merge(DEFAULTS, data)


def get(dotted: str, default=None):
    cur = load()
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return default if cur is None else cur


def claude_bin() -> str:
    return str(Path(get("claude_bin")).expanduser())


def model(task: str) -> str:
    return get(f"models.{task}", DEFAULTS["models"].get(task, "haiku"))


def project_meta(project: str) -> dict:
    """projects/<项目>.md 的 frontmatter。"""
    p = ROOT / "projects" / f"{project}.md"
    if not p.exists():
        return {}
    parts = p.read_text(encoding="utf-8").split("---")
    return (yaml.safe_load(parts[1]) or {}) if len(parts) >= 3 else {}


def remote_ssh(project: str) -> str | None:
    """远程项目的 ssh 主机别名（projects/<项目>.md 的 remote_outbox.ssh）。"""
    ro = project_meta(project).get("remote_outbox") or {}
    return ro.get("ssh") if isinstance(ro, dict) else None


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) > 1:
        print(get(sys.argv[1], ""))
    else:
        print(json.dumps(load(), ensure_ascii=False, indent=2))
