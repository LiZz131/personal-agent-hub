"""飞书公共工具：读 .env、建 client、发送 Markdown 卡片、跨进程 Claude 互斥锁。"""
from __future__ import annotations

import ctypes
import json
import logging
import sys
from contextlib import contextmanager
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import (CreateMessageRequest, CreateMessageRequestBody,
                                 ReplyMessageRequest, ReplyMessageRequestBody)

ROOT = Path(__file__).resolve().parent.parent
CARD_LIMIT = 12000  # 卡片内容上限约 30KB，中文按 3 字节留余量
log = logging.getLogger("feishu")


def load_env() -> dict:
    path = ROOT / ".env"
    if not path.exists():
        sys.exit("找不到 .env")
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_OWNER_OPEN_ID"):
        if not env.get(k):
            sys.exit(f".env 中 {k} 为空")
    return env


def domain(env) -> str:
    return lark.LARK_DOMAIN if env.get("FEISHU_DOMAIN", "feishu").lower() == "lark" else lark.FEISHU_DOMAIN


def make_client(env) -> lark.Client:
    return (lark.Client.builder().app_id(env["FEISHU_APP_ID"]).app_secret(env["FEISHU_APP_SECRET"])
            .domain(domain(env)).log_level(lark.LogLevel.WARNING).build())


def _card(md: str, title: str | None = None) -> str:
    if len(md) > CARD_LIMIT:
        md = md[:CARD_LIMIT] + "\n\n…（内容过长已截断，完整内容见本地日志）"
    card = {"schema": "2.0", "body": {"elements": [{"tag": "markdown", "content": md}]}}
    if title:
        card["header"] = {"title": {"tag": "plain_text", "content": title}, "template": "blue"}
    return json.dumps(card, ensure_ascii=False)


def _text(md: str) -> str:
    return json.dumps({"text": md[:CARD_LIMIT]}, ensure_ascii=False)


def send(client, receive_id: str, md: str, title: str | None = None, id_type: str = "open_id") -> bool:
    """发 Markdown 卡片；失败则退化为纯文本。"""
    for msg_type, content in (("interactive", _card(md, title)), ("text", _text((title + "\n\n" if title else "") + md))):
        req = CreateMessageRequest.builder().receive_id_type(id_type).request_body(
            CreateMessageRequestBody.builder().receive_id(receive_id).msg_type(msg_type).content(content).build()).build()
        resp = client.im.v1.message.create(req)
        if resp.success():
            return True
        log.warning("send %s failed: code=%s msg=%s", msg_type, resp.code, resp.msg)
    return False


def reply(client, message_id: str, md: str, title: str | None = None) -> bool:
    for msg_type, content in (("interactive", _card(md, title)), ("text", _text((title + "\n\n" if title else "") + md))):
        req = ReplyMessageRequest.builder().message_id(message_id).request_body(
            ReplyMessageRequestBody.builder().msg_type(msg_type).content(content).build()).build()
        resp = client.im.v1.message.reply(req)
        if resp.success():
            return True
        log.warning("reply %s failed: code=%s msg=%s", msg_type, resp.code, resp.msg)
    return False


def notify_owner(env, client, md: str, title: str | None = None) -> None:
    """推送给本人；若配置了 FEISHU_DIGEST_CHAT_IDS 则同时发到这些群。"""
    send(client, env["FEISHU_OWNER_OPEN_ID"], md, title)
    for chat in filter(None, (c.strip() for c in env.get("FEISHU_DIGEST_CHAT_IDS", "").split(","))):
        send(client, chat, md, title, id_type="chat_id")


# ---- 跨进程互斥：保证同一时刻只有一个 Claude 在改 wiki（与 run-job.ps1 共用同名 mutex）----
MUTEX_NAME = "Local\\PersonalAgentClaude"
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.restype = ctypes.c_void_p
_k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_k32.ReleaseMutex.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [ctypes.c_void_p]


@contextmanager
def claude_lock():
    h = _k32.CreateMutexW(None, False, MUTEX_NAME)
    r = _k32.WaitForSingleObject(h, 0xFFFFFFFF)
    if r not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
        _k32.CloseHandle(h)
        raise RuntimeError(f"mutex wait failed: {r}")
    try:
        yield
    finally:
        _k32.ReleaseMutex(h)
        _k32.CloseHandle(h)
