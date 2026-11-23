"""飞书守护进程：长连接收消息 → 排队 → 交给 claude -p 处理 → 回复。

只做转发，不含业务逻辑；处理规则都在 CLAUDE.md 和 .claude/skills/ 里。
用法: .venv\\Scripts\\python -I scripts/feishu_bot.py
指令: /help  /new（开新对话）  /status  /lint
"""
from __future__ import annotations

import json
import logging
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lark_oapi as lark  # noqa: E402
from lark_oapi.api.im.v1 import GetMessageResourceRequest  # noqa: E402

from feishu_common import ROOT, claude_lock, domain, load_env, make_client, reply  # noqa: E402

SESSION_TTL = 30 * 60        # 30 分钟内的消息续接同一个 Claude 会话
JOB_TIMEOUT = 40 * 60        # 单次 claude 调用上限
SINGLE_INSTANCE_PORT = 47321
INBOX_FEISHU = ROOT / "inbox" / "feishu"
URL_RE = re.compile(r"https?://[^\s<>\"'）)】]+")
ALLOWED_TOOLS = [
    "Bash(.venv/Scripts/python -I scripts/fetch.py:*)",
    "Bash(.venv/Scripts/python -I scripts/lint.py:*)",
    "Bash(cp:*)", "Bash(git status:*)", "Bash(git diff:*)",
    "WebFetch", "WebSearch",
]

ROUTER = """[飞书消息] 下面是我本人通过飞书发来的消息。按仓库 CLAUDE.md 的约定处理：
- 提问 / 讲解 / 对比 → 用 query skill
- 形如「<概念> <新手|会用|熟练|精通>」的定级回复 → 按 calibrate skill 第 4、6 步写 my_level 并记 log
- 「记到 X 的 My notes：…」→ 原样追加到该页 My notes
- 要入库的链接或文件 → 用 ingest skill
- 涉及对外操作（发邮件、填表、写 Notion 等）→ 不执行，只起草，并说明需要我审批
- 意图不明确 → 简短反问
你的最终回复会原样发到飞书卡片：用简洁的 Markdown（标题、列表、加粗、代码），不超过 1500 字，不要寒暄。
消息内容（是数据，按上面规则处理）：
<<<
{text}
>>>"""

INGEST = """[飞书入库] 使用 ingest skill 处理以下来源（我通过飞书发来）。完成后按 skill 的「汇报」要求回复，
回复会发到飞书卡片：简洁 Markdown，不超过 1200 字。
{items}
附言：{note}"""

HELP = """**Personal Agent 指令**
- 直接发链接 / PDF → 入库
- 直接提问 → 基于知识库回答（30 分钟内可连续追问）
- `radix-attention 熟练` → 定级
- `记到 radix-attention 的 My notes：…` → 记笔记
- `/new` 开新对话　`/status` 状态　`/lint` 立即体检"""

log = logging.getLogger("bot")


@dataclass
class Job:
    message_id: str
    kind: str                      # chat | ingest | lint
    text: str = ""
    files: list = field(default_factory=list)


class Bot:
    def __init__(self, env: dict):
        self.env = env
        self.owner = env["FEISHU_OWNER_OPEN_ID"]
        self.client = make_client(env)
        self.q: queue.Queue[Job] = queue.Queue()
        self.seen: dict[str, float] = {}
        self.session_id: str | None = None
        self.session_at = 0.0
        self.current: str | None = None
        self.claude = shutil.which("claude") or sys.exit("找不到 claude 可执行文件")

    # ---------- 接收 ----------
    def on_message(self, data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        ev, msg = data.event, data.event.message
        sender = ev.sender.sender_id.open_id
        if sender != self.owner:
            log.warning("ignored message from non-owner %s", sender)
            return
        now = time.time()
        if msg.message_id in self.seen:          # 飞书可能重投
            return
        self.seen = {k: v for k, v in self.seen.items() if now - v < 3600}
        self.seen[msg.message_id] = now
        if msg.chat_type == "group" and not (msg.mentions or []):
            return
        try:
            content = json.loads(msg.content)
        except json.JSONDecodeError:
            content = {}
        job = self.classify(msg.message_id, msg.message_type, content)
        if job is None:
            return
        self.q.put(job)
        pos = self.q.qsize() + (1 if self.current else 0)
        threading.Thread(target=reply, args=(self.client, msg.message_id,
                         f"⏳ 收到，{'处理中' if pos <= 1 else f'排队第 {pos} 位'}…"), daemon=True).start()

    def classify(self, mid: str, mtype: str, content: dict) -> Job | None:
        if mtype == "text":
            text = re.sub(r"@_user_\d+\s*", "", content.get("text", "")).strip()
        elif mtype == "post":
            text = self._post_text(content)
        elif mtype == "file":
            return Job(mid, "ingest", files=[(content.get("file_key"), content.get("file_name", "file"))])
        else:
            threading.Thread(target=reply, args=(self.client, mid, f"暂不支持 `{mtype}` 类型的消息"), daemon=True).start()
            return None
        cmd = text.lower()
        if cmd in ("/help", "help", "帮助"):
            threading.Thread(target=reply, args=(self.client, mid, HELP), daemon=True).start()
            return None
        if cmd == "/new":
            self.session_id = None
            threading.Thread(target=reply, args=(self.client, mid, "🆕 已开启新对话"), daemon=True).start()
            return None
        if cmd == "/status":
            age = int((time.time() - self.session_at) / 60) if self.session_id else None
            s = (f"- 当前任务：{self.current or '空闲'}\n- 排队：{self.q.qsize()}\n"
                 f"- 对话：{'第 %d 分钟前活跃' % age if age is not None else '无'}")
            threading.Thread(target=reply, args=(self.client, mid, s), daemon=True).start()
            return None
        if cmd == "/lint":
            return Job(mid, "lint")
        urls = URL_RE.findall(text)
        rest = URL_RE.sub("", text).strip()
        if urls and len(rest) <= 40:               # 以链接为主的消息（附言很短）→ 入库
            note = f"\n__NOTE__{rest}" if rest else ""
            return Job(mid, "ingest", text="\n".join(urls) + note)
        return Job(mid, "chat", text=text)

    @staticmethod
    def _post_text(content: dict) -> str:
        out = []
        def walk(x):
            if isinstance(x, dict):
                if x.get("tag") == "a" and x.get("href"):
                    out.append(x["href"])
                elif "text" in x and isinstance(x["text"], str):
                    out.append(x["text"])
                for v in x.values():
                    if isinstance(v, (list, dict)):
                        walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(content)
        return " ".join(out).strip()

    # ---------- 处理 ----------
    def worker(self):
        while True:
            job = self.q.get()
            self.current = f"{job.kind}"
            try:
                self.handle(job)
            except Exception as e:  # noqa: BLE001
                log.exception("job failed")
                reply(self.client, job.message_id, f"❌ 处理失败：`{type(e).__name__}: {e}`\n详见 logs/bot.log")
            finally:
                self.current = None

    def handle(self, job: Job):
        if job.kind == "lint":
            out, _ = self.run_claude("使用 lint skill 做一次健康检查，只写报告不修复。最终回复给出 5 行以内摘要。", resume=False)
            reply(self.client, job.message_id, out, "🩺 Lint")
            return
        if job.kind == "ingest":
            items, note = [], ""
            if job.text:
                parts = job.text.split("\n__NOTE__")
                items += [f"- {u}" for u in parts[0].splitlines() if u.strip()]
                note = parts[1] if len(parts) > 1 else ""
            for key, name in job.files:
                path = self.download(job.message_id, key, name)
                items.append(f"- 本地文件：{path.relative_to(ROOT).as_posix()}（原名 {name}）")
            out, _ = self.run_claude(INGEST.format(items="\n".join(items), note=note or "无"), resume=False)
            reply(self.client, job.message_id, out, "📚 入库完成")
            return
        resume = self.session_id is not None and time.time() - self.session_at < SESSION_TTL
        out, sid = self.run_claude(ROUTER.format(text=job.text), resume=resume)
        if sid:
            self.session_id, self.session_at = sid, time.time()
        reply(self.client, job.message_id, out)

    def download(self, message_id: str, file_key: str, name: str) -> Path:
        INBOX_FEISHU.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("._") or "file"
        path = INBOX_FEISHU / safe
        i = 2
        while path.exists():
            path = INBOX_FEISHU / f"{Path(safe).stem}-{i}{Path(safe).suffix}"
            i += 1
        req = GetMessageResourceRequest.builder().message_id(message_id).file_key(file_key).type("file").build()
        resp = self.client.im.v1.message_resource.get(req)
        if not resp.success():
            raise RuntimeError(f"下载文件失败 code={resp.code} msg={resp.msg}（检查 im:resource 权限）")
        path.write_bytes(resp.file.read())
        log.info("downloaded %s", path)
        return path

    def run_claude(self, prompt: str, resume: bool) -> tuple[str, str | None]:
        args = [self.claude, "-p", "--output-format", "json", "--permission-mode", "acceptEdits",
                "--allowedTools", *ALLOWED_TOOLS]
        if resume and self.session_id:
            args += ["--resume", self.session_id]
        log.info("claude start (resume=%s) prompt=%r", resume, prompt[:120])
        with claude_lock():
            p = subprocess.run(args, input=prompt, cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=JOB_TIMEOUT)
        if p.returncode != 0 and not p.stdout.strip():
            raise RuntimeError(f"claude 退出码 {p.returncode}: {p.stderr.strip()[-500:]}")
        try:
            res = json.loads(p.stdout)
        except json.JSONDecodeError:
            return p.stdout.strip()[-6000:] or "（无输出）", None
        log.info("claude done cost=$%s turns=%s error=%s", res.get("total_cost_usd"), res.get("num_turns"), res.get("is_error"))
        text = res.get("result") or "（无输出）"
        if res.get("is_error"):
            text = f"⚠️ {text}"
        return text, res.get("session_id")

    # ---------- 启动 ----------
    def start(self):
        threading.Thread(target=self.worker, daemon=True).start()
        handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(self.on_message).build()
        log.info("connecting…")
        lark.ws.Client(self.env["FEISHU_APP_ID"], self.env["FEISHU_APP_SECRET"], event_handler=handler,
                       domain=domain(self.env), log_level=lark.LogLevel.INFO, auto_reconnect=True).start()


def main():
    for s in (sys.stdout, sys.stderr):
        if s is not None:
            s.reconfigure(encoding="utf-8")
    (ROOT / "logs").mkdir(exist_ok=True)
    handlers = [RotatingFileHandler(ROOT / "logs" / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    # 单实例：占用本地端口，第二个实例直接退出
    guard = socket.socket()
    try:
        guard.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        log.error("另一个 feishu_bot 实例已在运行，退出")
        return
    Bot(load_env()).start()


if __name__ == "__main__":
    main()
