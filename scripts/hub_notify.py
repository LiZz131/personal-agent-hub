"""项目事件 → hub 事件日志 → Personal Agent 机器人发到「项目通知」群。

事件来源：本地 Claude Code 的 hook（scripts/cc_hook.py），以及远程服务器的发件箱（后续加入）。
每个事件都先追加写入 events/log.jsonl，这样 agent 能查到；再发到飞书。

用法：
  .venv\\Scripts\\python -I scripts\\hub_notify.py --send <事件文件.json>   # 由 cc_hook 在后台调用
  .venv\\Scripts\\python -I scripts\\hub_notify.py --test                    # 发一条测试通知
"""
from __future__ import annotations

import json
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:      # -I 模式下脚本目录不在 sys.path 里
    sys.path.insert(0, str(ROOT / "scripts"))
EVENTS = ROOT / "events"
LOG = EVENTS / "log.jsonl"
PENDING = EVENTS / "pending"
DEDUP = EVENTS / "dedup.json"

KIND_TITLE = {
    "approval": "⚠️ 需要审批",
    "waiting": "❓ 等你回复",
    "progress": "⏳ 阶段进展（还在继续）",
    "done": "⏸ 本轮结束",
    "checkpoint": "🧭 检查点",
    "test": "🔔 测试通知",
}


def load_env() -> dict:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m:
                env[m.group(1)] = m.group(2)
    return env


def projects() -> list[dict]:
    """读 projects/*.md 的 frontmatter：name、local_paths。"""
    import yaml

    out = []
    for p in sorted((ROOT / "projects").glob("*.md")):
        try:
            fm = yaml.safe_load(p.read_text(encoding="utf-8").split("---")[1]) or {}
        except Exception:  # noqa: BLE001
            continue
        out.append({"name": p.stem, "title": fm.get("title", p.stem),
                    "paths": [str(x) for x in fm.get("local_paths") or []]})
    return out


def project_for(cwd: str) -> str:
    norm = lambda s: s.replace("\\", "/").rstrip("/").lower()  # noqa: E731
    # 后台会话（claude --bg）跑在 <仓库>/.claude/worktrees/<名字>/ 里，归到所在仓库
    cwd = re.sub(r"[\\/]\.claude[\\/]worktrees[\\/][^\\/]+.*$", "", cwd or "")
    c = norm(cwd)
    best, best_len = "", 0
    for pr in projects():
        for path in pr["paths"]:
            n = norm(path)
            if (c == n or c.startswith(n + "/")) and len(n) > best_len:
                best, best_len = pr["name"], len(n)
    return best or (Path(cwd).name if cwd else "unknown")


def chat_for(project: str, env: dict) -> tuple[str, str]:
    """返回 (receive_id, id_type)。优先按项目分群，其次默认通知群，最后私聊本人。"""
    for pair in filter(None, (x.strip() for x in env.get("FEISHU_PROJECT_CHAT_IDS", "").split(","))):
        k, _, v = pair.partition("=")
        if k.strip() == project and v.strip():
            return v.strip(), "chat_id"
    if env.get("FEISHU_NOTIFY_CHAT_ID"):
        return env["FEISHU_NOTIFY_CHAT_ID"], "chat_id"
    return env["FEISHU_OWNER_OPEN_ID"], "open_id"


def should_send(event: dict, env: dict) -> bool:
    """同一会话、同一类事件在 NOTIFY_DEDUP_SECONDS 内只发一次。"""
    window = int(env.get("NOTIFY_DEDUP_SECONDS") or 120)
    key = f"{event.get('host', '')}|{event.get('session_id', '')}|{event['kind']}"
    try:
        state = json.loads(DEDUP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    now = time.time()
    state = {k: v for k, v in state.items() if now - v < 24 * 3600}
    if event["kind"] != "test" and now - state.get(key, 0) < window:
        return False
    state[key] = now
    DEDUP.write_text(json.dumps(state), encoding="utf-8")
    return True


def _short_path(fp: str, cwd: str) -> str:
    a, b = fp.replace("\\", "/"), (cwd or "").replace("\\", "/").rstrip("/")
    return a[len(b) + 1:] if b and a.lower().startswith(b.lower() + "/") else a


def render_turn(turn: dict, cwd: str) -> list[str]:
    """本轮做了什么：命令、改动、提交、后台任务、子 agent。不省略。"""
    out = []
    if turn.get("request"):
        out.append(f"**你的请求**：{turn['request']}")
    acts = []
    if turn.get("commits"):
        acts += [f"- 📦 提交：{c}" for c in turn["commits"]]
    if turn.get("files"):
        acts.append(f"- ✏️ 改动文件（{len(turn['files'])}）：" + "、".join(f"`{_short_path(f, cwd)}`" for f in turn["files"]))
    running = set(turn.get("bg_running") or [])
    for b in turn.get("background") or []:
        acts.append(f"- {'⏳ 后台运行中' if b in running else '✔️ 后台任务（已结束）'}：{b}")
    if turn.get("agents"):
        acts += [f"- 🤖 子 agent：{a}" for a in turn["agents"]]
    if turn.get("commands"):
        acts.append(f"- 🖥 执行命令（{len(turn['commands'])}）：")
        acts += [f"  {i}. {c}" for i, c in enumerate(turn["commands"], 1)]
    if turn.get("reads"):
        acts.append(f"- 📖 读取或搜索 {turn['reads']} 次")
    if acts:
        out.append("**本轮动作**：\n" + "\n".join(acts))
    return out


def render(event: dict) -> tuple[str, str]:
    title = f"{KIND_TITLE.get(event['kind'], event['kind'])} · {event.get('project', '?')}"
    lines = []
    if event.get("session_title"):
        lines.append(f"**会话**：{event['session_title']}")
    if event.get("message"):
        lines.append(f"**事件**：{event['message']}")
    if event.get("turn"):
        lines += render_turn(event["turn"], event.get("cwd", ""))
    if event.get("summary"):
        lines.append(f"**最后输出**：\n{event['summary']}")
    if event.get("elapsed_min") is not None:
        lines.append(f"**本轮用时**：约 {event['elapsed_min']} 分钟")
    where = event.get("host") or "本机"
    lines.append(f"<font color='grey'>{where} · {event.get('cwd', '')} · 会话 {str(event.get('session_id', ''))[:8]} · "
                 f"{time.strftime('%m-%d %H:%M', time.localtime(event['ts']))}</font>")
    return title, "\n".join(lines)


def record(event: dict) -> None:
    EVENTS.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


# ---- 轻量发送：只用标准库。lark-oapi SDK 光 import 就要 ~11s，会让每条通知都晚到 ----
TOKEN_CACHE = EVENTS / "token.json"


def _api_base(env: dict) -> str:
    return "https://open.larksuite.com" if env.get("FEISHU_DOMAIN", "feishu").lower() == "lark" else "https://open.feishu.cn"


def _post(url: str, payload: dict, token: str | None = None) -> dict:
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    return _open(req, 15)


def _open(req, timeout: int) -> dict:
    """飞书 API 出错时（4xx/5xx）返回它的错误 JSON（code/msg），不抛异常，调用方按 code 判断。"""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except ValueError:
            body = {}
        return {"code": body.get("code") or e.code, "msg": body.get("msg") or f"HTTP {e.code}"}


def tenant_token(env: dict) -> str:
    """tenant_access_token 有效期约 2 小时；缓存到 events/token.json（events/ 不进 git）。"""
    try:
        c = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
        if c.get("app_id") == env["FEISHU_APP_ID"] and c.get("expire_at", 0) > time.time() + 120:
            return c["token"]
    except (OSError, ValueError, KeyError):
        pass
    r = _post(f"{_api_base(env)}/open-apis/auth/v3/tenant_access_token/internal",
              {"app_id": env["FEISHU_APP_ID"], "app_secret": env["FEISHU_APP_SECRET"]})
    if r.get("code") != 0:
        raise RuntimeError(f"tenant_access_token failed: code={r.get('code')} msg={r.get('msg')}")
    EVENTS.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps({"app_id": env["FEISHU_APP_ID"], "token": r["tenant_access_token"],
                                       "expire_at": time.time() + int(r.get("expire", 7200))}), encoding="utf-8")
    return r["tenant_access_token"]


CARD_CHUNK = 7000   # 单张卡片的 Markdown 字符数；飞书卡片 JSON 上限约 30KB，中文按 3 字节留足余量


def split_markdown(md: str, limit: int = CARD_CHUNK) -> list[str]:
    """按段落切分长文本，绝不截断内容；单段过长时再按行、按字符切。"""
    if len(md) <= limit:
        return [md]
    parts, cur = [], ""
    for para in md.split("\n\n"):
        piece = para if not cur else "\n\n" + para
        if len(cur) + len(piece) <= limit:
            cur += piece
            continue
        if cur:
            parts.append(cur)
        while len(para) > limit:            # 超长段落：优先在换行处切
            cut = para.rfind("\n", 0, limit)
            cut = cut if cut > limit // 2 else limit
            parts.append(para[:cut])
            para = para[cut:].lstrip("\n")
        cur = para
    if cur:
        parts.append(cur)
    return parts


def _card(title: str, md: str, color: str) -> str:
    # update_multi：允许之后原地更新（状态卡、检查点卡标记「已处理」）
    return json.dumps({"schema": "2.0", "config": {"update_multi": True},
                       "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
                       "body": {"elements": [{"tag": "markdown", "content": md}]}}, ensure_ascii=False)


def send_card(env: dict, receive_id: str, id_type: str, title: str, md: str, color: str = "blue") -> list[str]:
    """发送卡片（过长时无损拆成多张），返回各条消息的 message_id；有失败时返回空列表。"""
    url = f"{_api_base(env)}/open-apis/im/v1/messages?receive_id_type={id_type}"
    chunks = split_markdown(md)
    ids, ok = [], True
    for i, chunk in enumerate(chunks):
        t = title if len(chunks) == 1 else f"{title}（{i + 1}/{len(chunks)}）"
        payload = {"receive_id": receive_id, "msg_type": "interactive", "content": _card(t, chunk, color)}
        r = _post(url, payload, tenant_token(env))
        if r.get("code") != 0:   # 卡片失败时退化为纯文本（同样不截断）
            payload = {"receive_id": receive_id, "msg_type": "text",
                       "content": json.dumps({"text": f"{t}\n\n{chunk}"}, ensure_ascii=False)}
            r = _post(url, payload, tenant_token(env))
        ok = ok and r.get("code") == 0
        if r.get("code") == 0:
            ids.append((r.get("data") or {}).get("message_id", ""))
    return ids if ok else []


FILE_LIMIT = 30 * 1024 * 1024   # 飞书 im/v1/files 单个文件上限
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
FILE_TYPES = {".pdf": "pdf", ".doc": "doc", ".docx": "doc", ".xls": "xls", ".xlsx": "xls",
              ".ppt": "ppt", ".pptx": "ppt", ".mp4": "mp4", ".opus": "opus"}


def _multipart(url: str, fields: dict, file_field: str, path: Path, token: str) -> dict:
    import mimetypes
    import urllib.request

    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{path.name}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n".encode("utf-8") + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(url, data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                                          "Authorization": f"Bearer {token}"})
    return _open(req, 300)


def _video_meta(p: Path) -> tuple[int, Path | None]:
    """视频时长（毫秒）和封面帧（第 1 秒，存到 events/thumbs/）。没有 ffmpeg 时返回 (0, None)。"""
    import shutil
    import subprocess

    nw = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    ms, cover = 0, None
    if probe := shutil.which("ffprobe"):
        try:
            out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                                 capture_output=True, timeout=30, creationflags=nw).stdout.decode().strip()
            ms = int(float(out) * 1000)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    if ff := shutil.which("ffmpeg"):
        thumbs = EVENTS / "thumbs"
        thumbs.mkdir(parents=True, exist_ok=True)
        cover = thumbs / f"{uuid.uuid4().hex}.jpg"
        try:
            subprocess.run([ff, "-y", "-v", "error", "-ss", "1", "-i", str(p), "-frames:v", "1", "-vf", "scale=640:-2", str(cover)],
                           capture_output=True, timeout=60, creationflags=nw)
        except (OSError, subprocess.TimeoutExpired):
            pass
        cover = cover if cover.is_file() else None
    return ms, cover


def send_file(env: dict, receive_id: str, id_type: str, path: str) -> str:
    """把本地文件作为附件发到飞书：图片直接显示，mp4 作为可播放的视频消息，其他是文件。成功返回空串，失败返回原因。"""
    p = Path(path)
    if not p.is_file():
        return "文件不存在"
    if p.stat().st_size > FILE_LIMIT:
        return f"超过 30 MB（{p.stat().st_size / 1048576:.0f} MB），未附上"
    try:
        base, token = _api_base(env), tenant_token(env)
        msg_url = f"{base}/open-apis/im/v1/messages?receive_id_type={id_type}"

        def post(msg_type: str, content: dict) -> dict:
            return _post(msg_url, {"receive_id": receive_id, "msg_type": msg_type, "content": json.dumps(content)}, token)

        def upload_file(file_type: str, extra: dict | None = None) -> dict:
            return _multipart(f"{base}/open-apis/im/v1/files",
                              {"file_type": file_type, "file_name": p.name, **(extra or {})}, "file", p, token)

        suffix = p.suffix.lower()
        if suffix in IMAGE_EXT:
            r = _multipart(f"{base}/open-apis/im/v1/images", {"image_type": "message"}, "image", p, token)
            if r.get("code") != 0:
                return f"上传失败：{r.get('msg')}"
            r = post("image", {"image_key": r["data"]["image_key"]})
        elif suffix == ".mp4":
            # 飞书：file_type=mp4 上传的文件只能用 msg_type=media 发（带封面），用 file 发会 400
            ms, cover = _video_meta(p)
            r = upload_file("mp4", {"duration": ms} if ms else None)
            if r.get("code") != 0:
                return f"上传失败：{r.get('msg')}"
            content = {"file_key": r["data"]["file_key"]}
            if cover:
                ri = _multipart(f"{base}/open-apis/im/v1/images", {"image_type": "message"}, "image", cover, token)
                cover.unlink(missing_ok=True)
                if ri.get("code") == 0:
                    content["image_key"] = ri["data"]["image_key"]
            r = post("media", content)
            if r.get("code") != 0:      # 视频消息发不出去时，退回为普通文件
                ru = upload_file("stream")
                r = post("file", {"file_key": ru["data"]["file_key"]}) if ru.get("code") == 0 else ru
        else:
            r = upload_file(FILE_TYPES.get(suffix, "stream"))
            if r.get("code") != 0:
                return f"上传失败：{r.get('msg')}"
            r = post("file", {"file_key": r["data"]["file_key"]})
        return "" if r.get("code") == 0 else f"发送失败：{r.get('msg')}"
    except Exception as e:  # noqa: BLE001  一个附件失败不能影响卡片和其他附件
        return f"{type(e).__name__}: {e}"


def update_card(env: dict, message_id: str, title: str, md: str, color: str = "blue") -> bool:
    """原地更新一张卡片（不会提醒）。内容超过单卡上限时返回 False，由调用方改发新卡片。"""
    import urllib.error
    import urllib.request

    if not message_id or len(md) > CARD_CHUNK:
        return False
    req = urllib.request.Request(f"{_api_base(env)}/open-apis/im/v1/messages/{message_id}", method="PATCH",
                                 data=json.dumps({"content": _card(title, md, color)}, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json; charset=utf-8",
                                          "Authorization": f"Bearer {tenant_token(env)}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8")).get("code") == 0
    except urllib.error.HTTPError:
        return False


KIND_COLOR = {"approval": "orange", "waiting": "yellow", "progress": "wathet", "done": "green", "test": "blue"}


def check_env(env: dict) -> None:
    for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_OWNER_OPEN_ID"):
        if not env.get(k):
            raise SystemExit(f".env 缺少 {k}")


def send_raw(event: dict, env: dict | None = None, note: str = "") -> bool:
    """直接把原始事件推到飞书（分诊关闭、分诊失败兜底、测试通知）。"""
    env = env or load_env()
    check_env(env)
    receive_id, id_type = chat_for(event.get("project", ""), env)
    title, body = render(event)
    if note:
        body = f"<font color='red'>{note}</font>\n{body}"
    return bool(send_card(env, receive_id, id_type, title, body, KIND_COLOR.get(event["kind"], "blue")))


def deliver(event: dict) -> bool:
    """记录事件，然后交给主控分诊（scripts/triage.py）决定是否、如何告诉我。
    .env 里 HUB_TRIAGE=0 时退回旧行为：每个事件直接推送原始通知。"""
    env = load_env()
    record(event)
    if event["kind"] == "test" or env.get("HUB_TRIAGE", "1") == "0":
        return send_raw(event, env) if should_send(event, env) else False
    from triage import enqueue
    enqueue(event)
    return True


def new_event(kind: str, **fields) -> dict:
    return {"id": uuid.uuid4().hex[:12], "ts": time.time(), "kind": kind, **fields}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if args[:1] == ["--send"] and len(args) == 2:
        p = Path(args[1])
        event = json.loads(p.read_text(encoding="utf-8"))
        try:
            deliver(event)
        finally:
            p.unlink(missing_ok=True)
    elif args[:1] == ["--test"]:
        ev = new_event("test", project="personal-agent", cwd=str(ROOT),
                       message="项目通知链路测试：hub 事件日志 → Personal Agent 机器人 → 群。", session_title="hub_notify --test")
        print("sent" if deliver(ev) else "deduplicated")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
