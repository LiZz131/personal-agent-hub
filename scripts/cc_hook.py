"""Claude Code hook：把「需要审批 / 等你回复 / 长任务完成」变成 hub 事件并推送到飞书。

在用户级 ~/.claude/settings.json 里注册给 Notification 和 Stop 两个事件，本机所有项目生效。
Claude Code 通过 stdin 传入 JSON（session_id、transcript_path、cwd、hook_event_name、message 等）。

要求：必须很快返回，不能拖慢 Claude Code，也绝不能让会话失败。
因此只做判断和写事件，推送交给后台进程（hub_notify.py --send）；任何异常都吞掉，退出码始终为 0。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


def real_user_message(rec: dict) -> bool:
    """用户真正输入的消息（排除工具结果、系统注入的元消息、子 agent）。"""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
        return False
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        s = content.lstrip()
        # 主控经跨会话消息派来的任务也算一轮的开始
        return bool(s) and (not s.startswith("<") or s.startswith("<cross-session-message"))
    if isinstance(content, list):
        return any(isinstance(c, dict) and c.get("type") == "text" for c in content)
    return False


def read_transcript(path: str) -> dict:
    """取会话标题，以及「本轮」（最后一条真实用户消息之后）的完整情况。只读文件尾部，保证速度。

    本轮信息：用户请求、执行过的命令（取 Bash 的 description）、改动的文件、git 提交、
    启动的后台任务、子 agent、读文件次数、最后一段 assistant 文本、本轮用时。
    """
    info = {"title": "", "last_user_ts": None, "last_text": "", "request": "", "commands": [],
            "files": [], "commits": [], "background": [], "bg_ids": {}, "bg_done": set(), "agents": [],
            "reads": 0, "last_ts": None}
    p = Path(path or "")
    if not p.is_file():
        return info
    size = p.stat().st_size
    with open(p, "rb") as f:
        f.seek(max(0, size - 3_000_000))
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        t = rec.get("type")
        if t == "ai-title" and rec.get("aiTitle"):
            info["title"] = rec["aiTitle"]
        elif t == "summary" and rec.get("summary") and not info["title"]:
            info["title"] = rec["summary"]
        elif real_user_message(rec) and rec.get("timestamp"):
            # 新的一轮开始：清空上一轮的动作记录
            info.update(last_user_ts=rec["timestamp"], request=_user_text(rec), last_text="", commands=[],
                        files=[], commits=[], background=[], bg_ids={}, bg_done=set(), agents=[], reads=0)
        elif "<task-notification>" in line:
            import re
            for tid, status in re.findall(r"<tool-use-id>([^<]+)</tool-use-id>.*?<status>([^<]+)</status>", line, re.S):
                if status.strip().lower() in ("completed", "failed", "killed", "stopped", "cancelled", "timeout", "error"):
                    info["bg_done"].add(tid.strip())
        if t == "assistant" and not rec.get("isSidechain"):
            info["last_ts"] = rec.get("timestamp") or info["last_ts"]
            content = (rec.get("message") or {}).get("content") or []
            texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            if any(x.strip() for x in texts):
                info["last_text"] = "\n".join(texts).strip()
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    _record_tool(info, c.get("name", ""), c.get("input") or {}, c.get("id", ""))
    return info


def _user_text(rec: dict) -> str:
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text").strip()


def _record_tool(info: dict, name: str, inp: dict, tool_id: str = "") -> None:
    import re

    if name in ("Bash", "PowerShell"):
        cmd = str(inp.get("command", ""))
        desc = str(inp.get("description") or cmd.splitlines()[0] if cmd else "").strip()
        if inp.get("run_in_background"):
            info["background"].append(desc)
            info["bg_ids"][tool_id] = desc
        else:
            info["commands"].append(desc)
        info["commits"] += commit_titles(cmd)
    elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        fp = str(inp.get("file_path") or inp.get("notebook_path") or "")
        if fp and fp not in info["files"]:
            info["files"].append(fp)
    elif name in ("Read", "Grep", "Glob"):
        info["reads"] += 1
    elif name in ("Agent", "Task"):
        info["agents"].append(str(inp.get("description") or inp.get("subagent_type") or "subagent"))
    elif name in ("Monitor", "ScheduleWakeup"):
        info["background"].append(str(inp.get("description") or name))
        info["bg_ids"][tool_id] = str(inp.get("description") or name)


def commit_titles(cmd: str) -> list[str]:
    """从一条 shell 命令里找出 git commit 的标题（第一行）。
    支持 -m "…" / -qm "…" / -qam '…' / -F - <<'MSG'（heredoc）/ -m "$(cat <<'EOF' …)"，以及 git -c k=v commit。"""
    import re

    titles = []
    for m in re.finditer(r"\bgit\b(?:\s+-c\s+\S+)*\s+commit\b", cmd):
        rest = cmd[m.end():m.end() + 2000]
        hd = re.search(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n([^\n]*)", rest)
        mm = re.search(r"\s-[a-zA-Z]*m\s*(\"((?:[^\"\\]|\\.)*)\"|'([^']*)')", rest)
        if mm and not mm.group(0).lstrip().startswith("-F") and "$(cat" not in (mm.group(2) or ""):
            msg = mm.group(2) if mm.group(2) is not None else mm.group(3)
            title = msg.replace("\\n", "\n").splitlines()[0] if msg else ""
        elif hd:
            title = hd.group(2)
        else:
            continue
        title = title.strip().strip("'\"")
        if title and title not in titles:
            titles.append(title)
    return titles


# 最后一段话里表示「还在进行、之后会继续」的说法
_IN_PROGRESS = ("next i'll", "next, i'll", "i'll check", "will report", "now runs", "now running", "is running",
                "still running", "waiting for", "in the background", "queued", "接下来", "下一步我", "正在运行", "正在跑",
                "后台运行", "等它跑完", "等结果", "跑完后", "完成后我", "稍后", "排队", "还要", "回来时", "去取结果")
_ASKING = ("请确认", "你决定", "你选", "你想要", "需要你确认", "需要你决定", "should i", "do you want", "would you like",
           "which option", "please confirm")


def running_background(info: dict) -> list[str]:
    """本轮启动、且还没收到完成通知的后台任务。"""
    return [d for tid, d in info.get("bg_ids", {}).items() if tid not in info.get("bg_done", set())]


def turn_status(info: dict) -> str:
    """done（本轮结束）/ progress（还在跑，之后会继续）/ question（在等你决定）。
    优先级：结尾是问号 → 有后台任务或「还在跑」的说法 → 有明确征询的说法 → 本轮结束。"""
    text = info.get("last_text", "").strip()
    tail = text[-400:].lower()
    if running_background(info):     # 还有后台任务（monitor / shell）在跑：先不当作「等你回复」，跑完它会继续，再停下时才提醒
        return "progress"
    if text.endswith(("?", "？")):
        return "question"
    if any(k in tail for k in _IN_PROGRESS):
        return "progress"
    if any(k in tail for k in _ASKING):
        return "question"
    return "done"


def minutes_since(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        t = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (time.time() - t.timestamp()) / 60


def clip(text: str, n: int = 400) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n].rstrip() + " …"


def is_idle_prompt(data: dict) -> bool:
    ntype = str(data.get("notification_type") or "").lower()
    return ntype == "idle_prompt" or "waiting for your input" in str(data.get("message") or "").lower()


def classify_notification(data: dict) -> str:
    ntype = str(data.get("notification_type") or "").lower()
    msg = str(data.get("message") or "").lower()
    if "permission" in ntype or "permission" in msg or "approve" in msg:
        return "approval"
    return "waiting"


def spawn_delivery(event: dict) -> None:
    pending = ROOT / "events" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    f = pending / f"{event['id']}.json"
    f.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
    pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    exe = str(pyw if pyw.exists() else sys.executable)
    cmd = [exe, "-I", str(ROOT / "scripts" / "hub_notify.py"), "--send", str(f)]
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    kw = dict(cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:   # 脱离 Claude Code 可能使用的 Job Object，避免 hook 返回后推送进程被一起结束
        subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kw)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(cmd, creationflags=flags, **kw)


def _new_event(kind: str, **fields) -> dict:
    import uuid
    return {"id": uuid.uuid4().hex[:12], "ts": time.time(), "kind": kind, **fields}


def build_event(data: dict, env: dict, remote: dict | None = None, info: dict | None = None) -> dict | None:
    """remote 不为 None 时运行在远程服务器上：项目名、主机名由参数给出，不依赖 hub 的其他文件。"""
    event_name = data.get("hook_event_name") or ""
    cwd = data.get("cwd") or os.getcwd()
    info = info if info is not None else read_transcript(data.get("transcript_path", ""))
    if remote is None:
        from hub_notify import project_for
        project, source, host = project_for(cwd), "local-hook", ""
    else:
        project, source, host = remote["project"], "remote-hook", remote["host"]
    new_event = _new_event
    common = dict(project=project, cwd=cwd, session_id=data.get("session_id", ""),
                  session_title=info["title"], source=source, **({"host": host} if host else {}))

    if event_name == "Notification":
        if is_idle_prompt(data):     # 「空闲 60 秒，等你输入」每轮结束都会出现，Stop 已经报告过这一轮
            return None
        kind = classify_notification(data)
        return new_event(kind, message=data.get("message", ""), summary=info["last_text"], **common)

    if event_name == "Stop":
        if data.get("stop_hook_active"):
            return None
        idle = minutes_since(info["last_user_ts"])
        threshold = float(env.get("NOTIFY_LONG_TASK_MINUTES") or 3)
        status = turn_status(info)
        if (idle is None or idle < threshold) and status != "question":
            return None          # 一问一答的短回合不报；但停下来等你回复的，无论多短都报（你希望飞书总能收到）
        kind = {"done": "done", "progress": "progress", "question": "waiting"}[status]
        turn_min = None
        if info["last_ts"]:
            turn_min = round(minutes_since(info["last_user_ts"]) - minutes_since(info["last_ts"]))
        return new_event(kind, summary=info["last_text"], elapsed_min=turn_min,
                         turn={**{k: info[k] for k in ("request", "commands", "files", "commits", "background", "agents", "reads")},
                               "bg_running": running_background(info)},
                         **common)
    return None


def parse_remote_args(argv: list[str]) -> dict | None:
    """--outbox DIR --project NAME --host LABEL 表示远程模式。"""
    if "--outbox" not in argv:
        return None
    get = lambda k, d="": argv[argv.index(k) + 1] if k in argv and argv.index(k) + 1 < len(argv) else d  # noqa: E731
    return {"outbox": get("--outbox"), "project": get("--project", "unknown"), "host": get("--host", "remote")}


def register_supervisor(raw: bytes) -> None:
    """cc-connect 启动的会话是飞书里的主控：登记下来，sessions.py 把它和项目会话分开显示。"""
    import hub_state as hs

    data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    state = {"SessionStart": "open", "UserPromptSubmit": "busy", "Stop": "idle",
             "SessionEnd": "closed"}.get(data.get("hook_event_name", ""))
    if state:
        hs.update_session(data.get("session_id", ""), state=state, driver="飞书主控", project="主控（飞书）",
                          cwd=data.get("cwd", ""), transcript_path=data.get("transcript_path", ""))


def trace(raw: bytes) -> None:
    """HUB_HOOK_TRACE=1 时记录每次调用（过滤之前），用于验证 hook 配置是否生效。"""
    if not os.environ.get("HUB_HOOK_TRACE"):
        return
    try:
        d = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        (ROOT / "events").mkdir(exist_ok=True)
        with open(ROOT / "events" / "hook_trace.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {d.get('hook_event_name')} entry={os.environ.get('CLAUDE_CODE_ENTRYPOINT')} "
                    f"cc={bool(os.environ.get('CC_SESSION_KEY'))} cwd={d.get('cwd')}\n")
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    try:
        raw = sys.stdin.buffer.read()
        trace(raw)
        # cc-connect 启动的会话本来就在飞书里回复；无头 claude -p（实验运行、定时任务、分诊）不打扰。
        # 例外：主控派出的工作会话（HUB_WORKER=1）要照常登记和上报。
        worker = bool(os.environ.get("HUB_WORKER"))
        if os.environ.get("CC_SESSION_KEY") and not parse_remote_args(sys.argv[1:]):
            register_supervisor(raw)    # 飞书里的主控会话：只登记，不上报事件
            return
        if not worker and os.environ.get("CLAUDE_CODE_ENTRYPOINT", "cli").startswith("sdk"):
            return
        data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        remote = parse_remote_args(sys.argv[1:])
        if remote:
            # 远程模式：只把事件写进发件箱，由 hub 定时只读拉取后统一推送。远程不保存任何飞书凭证。
            env = {"NOTIFY_LONG_TASK_MINUTES": os.environ.get("NOTIFY_LONG_TASK_MINUTES", "3")}
            event = build_event(data, env, remote)
            if event:
                outbox = Path(remote["outbox"]).expanduser()
                outbox.mkdir(parents=True, exist_ok=True)
                tmp = outbox / f".{event['id']}.tmp"
                tmp.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
                tmp.rename(outbox / f"{event['id']}.json")   # 原子落盘，避免 hub 读到半个文件
            return
        from hub_notify import load_env, project_for
        import hub_state as hs

        # 会话登记表（主控据此判断工作会话是关闭 / 在干活 / 在等输入，见 docs/design/supervisor.md §2）
        name = data.get("hook_event_name") or ""
        sid = data.get("session_id", "")
        cwd = data.get("cwd") or os.getcwd()
        base = dict(project=project_for(cwd), cwd=cwd, transcript_path=data.get("transcript_path", ""))
        if name == "SessionStart":
            hs.update_session(sid, state="open", source=data.get("source", ""),
                              driver="主控" if worker else "终端", **base)
            return
        if name == "UserPromptSubmit":
            hs.update_session(sid, state="busy", last_prompt=str(data.get("prompt", ""))[:300], **base)
            return
        if name == "SessionEnd":
            hs.update_session(sid, state="closed", end_reason=data.get("reason", ""), **base)
            return

        info = read_transcript(data.get("transcript_path", ""))
        if name == "Stop" and not data.get("stop_hook_active"):
            hs.update_session(sid, state="idle", title=info["title"], **base)
        elif name == "Notification":
            hs.update_session(sid, state="waiting", title=info["title"], **base)
        event = build_event(data, load_env(), info=info)
        if event:
            spawn_delivery(event)
    except Exception:  # noqa: BLE001  hook 绝不能让 Claude Code 出错
        try:
            (ROOT / "events").mkdir(exist_ok=True)
            import traceback
            with open(ROOT / "events" / "hook_errors.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + traceback.format_exc() + "\n")
            import incident
            incident.report_exc("cc_hook", log="events/hook_errors.log")
        except OSError:
            pass


if __name__ == "__main__":
    main()
    sys.exit(0)
