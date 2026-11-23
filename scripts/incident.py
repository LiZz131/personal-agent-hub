"""自动运维：报错 → 故障单（ops/incidents/）→ 交给开发负责 agent 修 → 修好后由主控如实汇报到飞书。

- 任何脚本出错时调用 report()（只记录、绝不抛异常）。同一个故障（指纹相同）只开一张单，后续只累加次数；
  已修复的故障再次出现会重新打开（recurring），再派一次。
- 派单（后台进程，不拖慢调用方）：
  1. 本机有正在运行的「开发负责 agent」→ 用无头 Haiku 通过 SendMessage 转告它；
     负责 agent = state/dev_owner.txt 里登记的会话名字；没登记时，取 cwd 是本仓库、非飞书主控的最近活跃会话。
  2. 没有 → 在本仓库起一个后台修复会话（claude --bg），按 ops-fix skill 处理（此时没有开发会话在改，不会冲突）。
  3. 同时给飞书发一张「🚨 故障」卡片（直接发送，不经过分诊：分诊本身可能就是故障源）。
- 修复会话只在本地提交，填好故障单的「根因 / 修复 / 验证」后运行 close，脚本把这几节原文发成「✅ 已修复」卡片（如实汇报，不改写），
  并记入「待我决定」；我回复「通过」后主控运行 approve 推送。
- 每天最多派 MAX_DISPATCH 次，超出的只记录，由早报提醒。

用法:
  incident.py report <来源> "<一句话现象>" [--file <日志>]       # 给 PowerShell 等非 Python 调用方
  incident.py close <INC-id> [--commit <sha> ...] [--status fixed|wontfix|needs-user]
  incident.py approve <INC-id>                                  # 我「通过」后：普通推送修复提交，标记已处理
  incident.py owner [<会话名字> | --clear]                      # 登记 / 查看开发负责 agent
  incident.py list [--all]
  incident.py dispatch <INC-id>                                 # report() 内部使用
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hub_config as hc  # noqa: E402
import hub_state as hs  # noqa: E402

ROOT = hs.ROOT
OPS = ROOT / "ops"
INC = OPS / "incidents"
INDEX = OPS / "INCIDENTS.md"
OWNER = hs.STATE / "dev_owner.txt"
SELF_LOG = ROOT / "events" / "incident_errors.log"
DISPATCH_LOG = ROOT / "events" / "incident_dispatch.jsonl"
CLAUDE = hc.claude_bin()
MAX_DISPATCH = 6
OPEN_STATES = ("open", "investigating", "recurring")
SECRET_RE = re.compile(r"(Bearer\s+|app_secret[\"'=:\s]+|token[\"'=:\s]+|t-g[0-9a-z]{8,})[\w.\-]+", re.I)


# ---------------- 故障单文件 ----------------
def _load(path: Path) -> tuple[dict, str]:
    import yaml

    _, fm, body = path.read_text(encoding="utf-8").split("---", 2)
    return yaml.safe_load(fm) or {}, body


def _save(path: Path, meta: dict, body: str) -> None:
    import yaml

    INC.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text("---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---" + body, encoding="utf-8")
    os.replace(tmp, path)


def find(inc_id: str) -> Path | None:
    hits = sorted(INC.glob(f"{inc_id}*.md")) if INC.exists() else []
    return hits[0] if hits else None


def all_incidents() -> list[tuple[Path, dict]]:
    out = []
    for p in sorted(INC.glob("INC-*.md")) if INC.exists() else []:
        try:
            out.append((p, _load(p)[0]))
        except (OSError, ValueError):
            continue
    return out


def section(body: str, name: str) -> str:
    m = re.search(rf"^## {name}\s*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    text = m.group(1).strip() if m else ""
    return "" if text.startswith("<!--") and text.endswith("-->") else text


def fingerprint(source: str, error: str, detail: str) -> str:
    """来源 + 异常类型 + 最后一个本仓库栈帧（数字、路径、引号内容归一化），同一处 bug 指纹相同。"""
    frames = re.findall(r'File "([^"]+)", line \d+, in (\w+)', detail)
    root = str(ROOT).lower()
    frame = next((f"{Path(f).name}:{fn}" for f, fn in reversed(frames) if f.lower().startswith(root)), "")
    etype = (re.findall(r"^(\w+(?:\.\w+)*(?:Error|Exception|Exit))\b", detail, re.M) or [""])[-1]
    norm = re.sub(r"\d+|'[^']*'|\"[^\"]*\"|[A-Za-z]:\\\S+", "#", error)[:120] if not (frame or etype) else ""
    return hashlib.sha1(f"{source}|{etype}|{frame}|{norm}".encode()).hexdigest()[:12]


def redact(text: str) -> str:
    return SECRET_RE.sub(lambda m: m.group(1) + "***", text)


TEMPLATE = """
# {id} {title}

## 现象
- 来源：`{source}`
- 首次：{first}；日志：{log}

```
{excerpt}
```

## 根因
<!-- 修复会话填写：根因是什么，证据是什么（复现步骤、日志行、代码位置 file:line） -->

## 修复
<!-- 修复会话填写：改了什么、为什么这样改；没修的写原因 -->

## 验证
<!-- 修复会话填写：怎么验证的（复现命令、测试、实际发送结果），结果如何 -->

## 时间线
- {first} 打开（{source}）
"""


def report(source: str, error: str, detail: str = "", log: str = "") -> str:
    """记录一次报错。返回故障单 id；本函数绝不抛异常。"""
    try:
        return _report(source, error, detail, log)
    except Exception:  # noqa: BLE001
        _self_log()
        return ""


def _report(source: str, error: str, detail: str, log: str) -> str:
    if os.environ.get("HUB_INCIDENT_OFF") == "1":
        return ""
    detail = redact(detail)
    error = redact(error.strip().splitlines()[0][:200] if error.strip() else "未知错误")
    fp = fingerprint(source, error, detail)
    now = hs.stamp()
    for path, meta in all_incidents():
        if meta.get("fingerprint") != fp or meta.get("status") in ("wontfix",):
            continue
        meta, body = _load(path)
        meta["count"] = int(meta.get("count", 1)) + 1
        meta["last_seen"] = now
        reopen = meta.get("status") == "fixed"
        if reopen:
            meta["status"] = "recurring"
            body = body.rstrip() + f"\n- {now} 修复后再次出现，重新打开\n"
        _save(path, meta, body)
        write_index()
        if reopen:
            spawn_dispatch(meta["id"])
        return meta["id"]
    day = time.strftime("%Y%m%d")
    n = 1 + sum(1 for p in INC.glob(f"INC-{day}-*.md")) if INC.exists() else 1
    inc_id = f"INC-{day}-{n:02d}"
    slug = re.sub(r"[^a-z0-9]+", "-", f"{source} {error}".lower()).strip("-")[:40] or "error"
    excerpt = "\n".join((detail or error).strip().splitlines()[-25:])
    meta = {"id": inc_id, "title": error, "status": "open", "source": source, "fingerprint": fp,
            "first_seen": now, "last_seen": now, "count": 1, "owner": "", "commits": []}
    _save(INC / f"{inc_id}-{slug}.md", meta,
          TEMPLATE.format(id=inc_id, title=error, source=source, first=now, log=log or "（无）", excerpt=excerpt))
    write_index()
    spawn_dispatch(inc_id)
    return inc_id


def report_exc(source: str, log: str = "") -> str:
    """在 except 块里调用：自动带上当前异常的 traceback。"""
    try:
        etype, e, _ = sys.exc_info()
        return report(source, f"{etype.__name__ if etype else 'Error'}: {e}", traceback.format_exc(), log)
    except Exception:  # noqa: BLE001
        _self_log()
        return ""


def _self_log() -> None:
    try:
        SELF_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SELF_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + traceback.format_exc() + "\n")
    except OSError:
        pass


def write_index() -> None:
    rows = sorted(all_incidents(), key=lambda x: (x[1].get("status") not in OPEN_STATES, x[1].get("id", "")), reverse=False)
    lines = ["# 故障单索引", "", "<!-- incident.py 自动生成；每张单的详情见 incidents/ -->", "",
             "| 单号 | 状态 | 来源 | 现象 | 次数 | 最近 | 提交 |", "|---|---|---|---|---|---|---|"]
    for p, m in rows:
        commits = " ".join(str(c)[:7] for c in m.get("commits") or [])
        title = str(m.get("title", "")).replace("|", "\\|")[:80]
        lines.append(f"| [{m.get('id')}](incidents/{p.name}) | {m.get('status')} | {m.get('source')} | {title} | "
                     f"{m.get('count', 1)} | {m.get('last_seen', '')} | {commits} |")
    OPS.mkdir(parents=True, exist_ok=True)
    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------- 派单 ----------------
def spawn_dispatch(inc_id: str) -> None:
    pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    cmd = [str(pyw if pyw.exists() else sys.executable), "-I", str(Path(__file__).resolve()), "dispatch", inc_id]
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0
    kw = dict(cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:
        subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kw)   # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(cmd, creationflags=flags, **kw)


def dispatched_today() -> int:
    if not DISPATCH_LOG.exists():
        return 0
    today = time.strftime("%Y-%m-%d")
    return sum(1 for ln in DISPATCH_LOG.read_text(encoding="utf-8").splitlines() if f'"{today}' in ln)


def dev_owner() -> dict | None:
    """正在运行的开发负责 agent：登记的名字优先；否则取 cwd 为本仓库、非飞书主控的最近活跃会话。"""
    from sessions import live_agents

    agents = [a for a in live_agents() if a.get("name")]
    want = OWNER.read_text(encoding="utf-8").strip() if OWNER.exists() else ""
    if want:
        hit = [a for a in agents if a.get("name") == want or a.get("sessionId", "").startswith(want)]
        if hit:
            return hit[0]
    root = str(ROOT).lower().rstrip("\\/")
    cands = []
    for a in agents:
        cwd = str(a.get("cwd", "")).lower().rstrip("\\/")
        rec = {}
        try:
            rec = json.loads((hs.SESSIONS / f"{a.get('sessionId', '')}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if cwd == root and rec.get("driver") != "飞书主控" and not str(a.get("name", "")).startswith("fix-"):
            cands.append((rec.get("updated", 0), a))
    return max(cands, key=lambda x: x[0])[1] if cands else None


FIX_PROMPT = """运维（自动）：出现了新的故障，请按 ops-fix skill 处理。
故障单：{path}
单号：{id} · 来源 {source} · 已出现 {count} 次
现象：{title}
完成后运行 `.venv/Scripts/python -I scripts/incident.py close {id} --commit <sha>`，主控会把结果如实汇报给用户。"""


def relay(name: str, msg: str) -> bool:
    from worker import FORWARD

    try:
        p = subprocess.run([CLAUDE, "-p", "--model", hc.model("relay"), "--no-session-persistence", "--strict-mcp-config",
                            "--setting-sources", "project", "--allowedTools", "SendMessage", "ListAgents"],
                           input=FORWARD.format(name=name, msg=msg).encode("utf-8"), capture_output=True, timeout=180,
                           cwd=str(ROOT / "events" / "triage_cwd"), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "SENT" in p.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return False


def dispatch(inc_id: str) -> None:
    path = find(inc_id)
    if not path:
        return
    meta, body = _load(path)
    msg = FIX_PROMPT.format(path=path, id=inc_id, source=meta.get("source"), count=meta.get("count", 1), title=meta.get("title"))
    if dispatched_today() >= MAX_DISPATCH:
        how = f"今天已派单 {MAX_DISPATCH} 次，达到上限：只记录，等你决定"
    else:
        owner = dev_owner()
        if owner and relay(owner["name"], msg):
            how = f"已转告开发负责 agent「{owner['name']}」"
            meta["owner"] = owner["name"]
        else:
            from worker import run_bg

            # 没有开发会话在跑时才起修复会话，所以直接在工作副本里改（不会和我的终端会话冲突）
            name = f"fix-{inc_id.lower()}"
            try:
                bg, _ = run_bg(str(ROOT), ["-n", name, "--disallowedTools", "Bash(git push:*)"], msg)   # 推送等我确认
                how = f"已起后台修复会话「{name}」（{bg}）"
                meta["owner"] = name
            except SystemExit as e:
                how = f"派单失败：{e}"
        with open(DISPATCH_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": hs.stamp(), "id": inc_id, "how": how}, ensure_ascii=False) + "\n")
    meta["status"] = "investigating" if meta.get("owner") and meta.get("status") == "open" else meta.get("status")
    body = body.rstrip() + f"\n- {hs.stamp()} {how}\n"
    _save(path, meta, body)
    write_index()
    recurring = "（修复后再次出现）" if meta.get("status") == "recurring" else ""
    m = re.search(r"```\n(.*?)\n```", section(body, "现象"), re.S)
    excerpt = (m.group(1) if m else str(meta.get("title")))[-1500:]
    _card(f"🚨 故障 {inc_id}{recurring}：{meta.get('title')}",
          f"**现象**\n来源 `{meta.get('source')}` · 已出现 {meta.get('count', 1)} 次\n```\n{excerpt}\n```\n\n"
          f"**处理**\n{how}\n\n<font color='grey'>故障单 ops/incidents/{path.name} · 修好后会发「✅ 已修复」</font>", "red",
          f"🚨 故障 {inc_id}：{meta.get('title')} — {how}")


def _card(title: str, md: str, color: str, line: str, pending: str = "") -> str:
    """直接发到 Personal_Agent 的通知群，并记进它的今日动态（不经过分诊）。
    pending 非空时这张卡需要我答复：分配 #Personal_Agent-<n> 标签，记入「待我决定」，返回标签。"""
    from hub_notify import chat_for, load_env, send_card

    env = load_env()
    rid, idt = chat_for("Personal_Agent", env)
    st = hs.load_state("Personal_Agent")
    tag = ""
    if pending:
        tag = f"#Personal_Agent-{st['meta'].get('next_tag', 1)}"
        st["meta"]["next_tag"] = int(st["meta"].get("next_tag", 1)) + 1
        md += f"\n\n<font color='grey'>{tag} · 回复或引用这条消息告诉主控</font>"
    ids = send_card(env, rid, idt, title, md, color)
    if pending and ids:
        cards = ROOT / "events" / "cards"
        cards.mkdir(parents=True, exist_ok=True)
        (cards / f"{ids[0]}.json").write_text(json.dumps({"title": title, "body": md}, ensure_ascii=False), encoding="utf-8")
        st["sections"]["待我决定"].append(f"- {tag} | {hs.stamp()} | {pending} | 会话  | msg {ids[0]}")
    st["sections"]["今日动态"].append(f"- {hs.stamp()} {line}" + (f"（{tag}）" if tag else ""))
    hs.save_state("Personal_Agent", st)
    return tag


# ---------------- 关单 ----------------
def close(inc_id: str, commits: list[str], status: str = "fixed") -> str:
    path = find(inc_id)
    if not path:
        return f"没有故障单 {inc_id}"
    meta, body = _load(path)
    cause, fix, verify = section(body, "根因"), section(body, "修复"), section(body, "验证")
    if status == "fixed" and not (cause and fix and verify):
        return "关单前先在故障单里填好「根因」「修复」「验证」三节（如实写，未验证就写未验证）"
    meta["status"] = status
    meta["commits"] = list(dict.fromkeys([*(meta.get("commits") or []), *commits]))
    meta["closed"] = hs.stamp()
    body = body.rstrip() + f"\n- {hs.stamp()} {status}" + (f"（提交 {' '.join(c[:7] for c in commits)}）" if commits else "") + "\n"
    _save(path, meta, body)
    write_index()
    head = {"fixed": "✅ 已修复", "wontfix": "⏹ 不修", "needs-user": "🙋 需要你决定"}.get(status, status)
    parts = [f"**根因**\n{cause or '（未填写）'}", f"**修复**\n{fix or '（未填写）'}", f"**验证**\n{verify or '（未填写）'}"]
    if meta["commits"]:
        parts.append("**提交**\n" + "\n".join(f"- `{c}`" for c in meta["commits"]))
    parts.append(f"<font color='grey'>{inc_id} · 来源 {meta.get('source')} · 共出现 {meta.get('count', 1)} 次 · "
                 f"处理者 {meta.get('owner') or '?'} · 故障单 ops/incidents/{path.name}</font>")
    pending = ""
    unpushed = [c for c in meta["commits"] if not _pushed(c)]
    if status == "fixed" and unpushed:   # 修复只在本地提交；我回复「通过」后主控再推送
        parts.insert(-1, f"**待你确认**\n已在本地提交（{' '.join(c[:7] for c in unpushed)}），**还没推送**。"
                         "回复「通过」后主控推送；有问题直接说，主控转给处理者。")
        pending = f"故障修复待推送：{inc_id} {meta.get('title')}"
    elif status == "needs-user":
        pending = f"故障需要你决定：{inc_id} {meta.get('title')}"
    tag = _card(f"{head} {inc_id}：{meta.get('title')}", "\n\n".join(parts), "green" if status == "fixed" else "orange",
                f"{head} {inc_id}：{meta.get('title')}", pending)
    if tag:
        meta, body = _load(path)
        meta["tag"] = tag
        _save(path, meta, body)
    return f"{inc_id} → {status}，已发飞书" + (f"（{tag} 等你确认）" if tag else "")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _pushed(commit: str) -> bool:
    """提交已经在远端分支上（git branch -r --contains 非空）。"""
    r = _git("branch", "-r", "--contains", commit)
    return r.returncode == 0 and bool(r.stdout.strip())


def approve(inc_id: str) -> str:
    """我回复「通过」后由主控调用：推送这张单的修复提交（普通推送，绝不强推），并把待决事项标为已处理。"""
    path = find(inc_id)
    if not path:
        return f"没有故障单 {inc_id}"
    meta, body = _load(path)
    commits = meta.get("commits") or []
    if commits and _git("merge-base", "--is-ancestor", commits[-1], "HEAD").returncode != 0:
        return f"{commits[-1][:7]} 不在当前分支上，没有推送；请检查"
    if any(not _pushed(c) for c in commits):
        r = _git("push")
        if r.returncode != 0:
            return f"推送失败：{(r.stderr or r.stdout).strip()[-300:]}"
    meta["pushed"] = hs.stamp()
    _save(path, meta, body.rstrip() + f"\n- {hs.stamp()} 我确认，已推送\n")
    write_index()
    if meta.get("tag"):
        import triage
        triage.resolve("Personal_Agent", meta["tag"], f"已确认并推送 {' '.join(c[:7] for c in commits)}")
    return f"{inc_id} 已推送（{' '.join(c[:7] for c in commits)}）"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    if cmd == "report" and len(a) >= 3:
        log = a[a.index("--file") + 1] if "--file" in a else ""
        detail = ""
        if log and Path(log).is_file():
            detail = "\n".join(Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
        print(report(a[1], a[2], detail, log))
    elif cmd == "dispatch" and len(a) == 2:
        try:
            dispatch(a[1])
        except Exception:  # noqa: BLE001
            _self_log()
    elif cmd == "close" and len(a) >= 2:
        commits = [a[i + 1] for i, x in enumerate(a) if x == "--commit" and i + 1 < len(a)]
        status = a[a.index("--status") + 1] if "--status" in a else "fixed"
        print(close(a[1], commits, status))
    elif cmd == "approve" and len(a) == 2:
        print(approve(a[1]))
    elif cmd == "owner":
        if "--clear" in a:
            OWNER.unlink(missing_ok=True)
            print("已清除登记，改为自动判断")
        elif len(a) >= 2:
            hs.STATE.mkdir(parents=True, exist_ok=True)
            OWNER.write_text(a[1], encoding="utf-8")
            print(f"开发负责 agent：{a[1]}")
        else:
            o = dev_owner()
            print(f"当前开发负责 agent：{o['name']}（{o.get('sessionId', '')[:8]}）" if o else "当前没有正在运行的开发负责 agent（出错时会起后台修复会话）")
    elif cmd == "list":
        for p, m in all_incidents():
            if "--all" in a or m.get("status") in OPEN_STATES or m.get("status") == "needs-user":
                print(f"- {m.get('id')} [{m.get('status')}] {m.get('source')} · {m.get('title')} · ×{m.get('count', 1)} · {p.name}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
