"""主控的共享记忆：会话登记表 + 每个项目的状态文件。

- 会话登记表：events/sessions/<session_id>.json，每个会话一个文件（多个 hook 并发写也不冲突）。
  由 cc_hook 在 SessionStart / UserPromptSubmit / Stop / SessionEnd 时更新。
- 项目状态：state/<project>.md（不进 git，由 agent 自由维护），分诊运行和飞书里的主控会话都读写它。
  frontmatter：status_card（当天状态卡的飞书消息 id）、next_tag（检查点序号）。
  各节：待我决定 / 工作会话 / 今日动态（显示在状态卡上）/ 记录（静默事件，不上卡片）。

设计见 docs/design/supervisor.md。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = ROOT / "events" / "sessions"
STATE = ROOT / "state"
SECTIONS = ("待我决定", "工作会话", "今日动态", "记录")
KEEP_DAYS = 3          # 今日动态、记录保留的天数
STALE_HOURS = 6        # 只有 open 没有 closed、transcript 又这么久没写入的会话，视为已关闭


# ---------------- 会话登记表 ----------------
def update_session(session_id: str, **fields) -> dict:
    if not session_id:
        return {}
    SESSIONS.mkdir(parents=True, exist_ok=True)
    p = SESSIONS / f"{session_id}.json"
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = {"session_id": session_id, "started": time.time()}
    rec.update({k: v for k, v in fields.items() if v not in (None, "")}, updated=time.time())
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return rec


def sessions(project: str | None = None, include_closed: bool = False) -> list[dict]:
    out = []
    now = time.time()
    for p in SESSIONS.glob("*.json") if SESSIONS.exists() else []:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("state") != "closed":
            tp = Path(rec.get("transcript_path") or "")
            last = max(rec.get("updated", 0), tp.stat().st_mtime if tp.is_file() else 0)
            if now - last > STALE_HOURS * 3600:
                rec["state"] = "closed"
        if project and rec.get("project") != project:
            continue
        if rec.get("state") == "closed" and not include_closed:
            continue
        out.append(rec)
    return sorted(out, key=lambda r: r.get("updated", 0), reverse=True)


def prune_sessions(days: int = 14) -> None:
    for p in SESSIONS.glob("*.json") if SESSIONS.exists() else []:
        if time.time() - p.stat().st_mtime > days * 86400:
            p.unlink(missing_ok=True)


IGNORED = STATE / "ignored_sessions.txt"   # 我说过「忽略」的会话：每行一个会话 id（或前缀），# 后是备注


def ignored_sessions() -> list[str]:
    if not IGNORED.exists():
        return []
    return [ln.split("#")[0].strip() for ln in IGNORED.read_text(encoding="utf-8").splitlines() if ln.split("#")[0].strip()]


def is_ignored(session_id: str) -> bool:
    return bool(session_id) and any(session_id.startswith(x) for x in ignored_sessions())


def ignore_session(session_id: str, note: str = "") -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    if not is_ignored(session_id):
        with open(IGNORED, "a", encoding="utf-8") as f:
            f.write(f"{session_id}  # {stamp()} {note}\n")


# ---------------- 项目状态文件 ----------------
def state_path(project: str) -> Path:
    safe = re.sub(r"[^\w.-]+", "_", project or "unknown")
    return STATE / f"{safe}.md"


def load_state(project: str) -> dict:
    """返回 {"meta": {...}, "sections": {名字: [行, ...]}}。"""
    import yaml

    st = {"meta": {"project": project, "next_tag": 1}, "sections": {s: [] for s in SECTIONS}}
    p = state_path(project)
    if not p.exists():
        return st
    text = p.read_text(encoding="utf-8")
    if text.startswith("---"):
        _, fm, text = text.split("---", 2)
        st["meta"].update(yaml.safe_load(fm) or {})
    cur = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            st["sections"].setdefault(cur, [])
        elif cur and line.strip():
            st["sections"][cur].append(line.rstrip())
    return st


def save_state(project: str, st: dict) -> None:
    import yaml

    STATE.mkdir(parents=True, exist_ok=True)
    st["meta"]["updated"] = time.strftime("%Y-%m-%dT%H:%M")
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - KEEP_DAYS * 86400))
    for sec in ("今日动态", "记录"):   # 行首是 "- YYYY-MM-DD HH:MM"；超过保留期的删掉
        st["sections"][sec] = [ln for ln in st["sections"].get(sec, [])
                               if not re.match(r"^- \d{4}-\d\d-\d\d ", ln) or ln[2:12] >= cutoff]
    body = ["---", yaml.safe_dump(st["meta"], allow_unicode=True, sort_keys=False).strip(), "---",
            f"# {st['meta'].get('project')} · 主控状态", "",
            "<!-- 由主控（分诊 + 飞书会话）维护；设计见 docs/design/supervisor.md -->", ""]
    for sec, lines in st["sections"].items():
        body += [f"## {sec}", *lines, ""]
    p = state_path(project)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text("\n".join(body), encoding="utf-8")
    os.replace(tmp, p)


def render_sessions(project: str) -> list[str]:
    lines = []
    for r in sessions(project):
        when = time.strftime("%m-%d %H:%M", time.localtime(r.get("updated", 0)))
        state = {"open": "打开", "busy": "干活中", "idle": "等输入", "waiting": "等你回复"}.get(r.get("state", ""), r.get("state", "?"))
        lines.append(f"- {str(r['session_id'])[:8]} | {r.get('driver', '终端')} | {state} | "
                     f"{r.get('host') or '本机'} {r.get('cwd', '')} | 「{r.get('title', '')}」 | {when}")
    return lines


def today_lines(st: dict) -> list[str]:
    today = time.strftime("%Y-%m-%d")
    return [ln for ln in st["sections"].get("今日动态", []) if ln.startswith(f"- {today} ")]


def stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def project_rules(project: str) -> str:
    """projects/<p>.md 里「分诊规则」一节（我的反馈，进 git）。"""
    p = ROOT / "projects" / f"{project}.md"
    if not p.exists():
        return ""
    m = re.search(r"^## 分诊规则\s*\n(.*?)(?=^## |\Z)", p.read_text(encoding="utf-8"), re.S | re.M)
    return m.group(1).strip() if m else ""


def user_idle_minutes() -> float | None:
    """本机键盘鼠标空闲多久（Windows GetLastInputInfo）。拿不到时返回 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        li = LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(li)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
            return None
        return round(((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 60000, 1)
    except Exception:  # noqa: BLE001
        return None
