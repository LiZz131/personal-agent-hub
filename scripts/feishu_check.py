"""飞书接入自检工具（不含任何业务逻辑）。

用法（在仓库根目录）:
  .venv\\Scripts\\python -I scripts/feishu_check.py token    # 检查 App ID / Secret 是否正确
  .venv\\Scripts\\python -I scripts/feishu_check.py listen   # 建立长连接；收到消息时打印发送者 open_id 并回复
  .venv\\Scripts\\python -I scripts/feishu_check.py send     # 向 FEISHU_OWNER_OPEN_ID 发送一条测试消息
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.auth.v3 import InternalTenantAccessTokenRequest, InternalTenantAccessTokenRequestBody
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody, ReplyMessageRequest, ReplyMessageRequestBody

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> dict:
    path = ROOT / ".env"
    if not path.exists():
        sys.exit("找不到 .env：先执行 copy .env.example .env 并填写")
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        if not env.get(k):
            sys.exit(f".env 中 {k} 为空")
    return env


def domain(env) -> str:
    return lark.LARK_DOMAIN if env.get("FEISHU_DOMAIN", "feishu").lower() == "lark" else lark.FEISHU_DOMAIN


def client(env) -> lark.Client:
    return (lark.Client.builder().app_id(env["FEISHU_APP_ID"]).app_secret(env["FEISHU_APP_SECRET"])
            .domain(domain(env)).log_level(lark.LogLevel.WARNING).build())


def cmd_token(env):
    req = InternalTenantAccessTokenRequest.builder().request_body(
        InternalTenantAccessTokenRequestBody.builder()
        .app_id(env["FEISHU_APP_ID"]).app_secret(env["FEISHU_APP_SECRET"]).build()).build()
    resp = client(env).auth.v3.tenant_access_token.internal(req)
    if resp.success():
        print("✅ App ID / Secret 正确，已拿到 tenant_access_token")
    else:
        print(f"❌ 失败 code={resp.code} msg={resp.msg}")
        print("   常见原因：ID/Secret 填错或有多余空格；FEISHU_DOMAIN 与应用所在平台不一致")


def cmd_listen(env):
    cli = client(env)
    owner = env.get("FEISHU_OWNER_OPEN_ID", "")

    def on_message(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        ev = data.event
        oid = ev.sender.sender_id.open_id
        msg = ev.message
        print(f"\n📩 收到消息  chat_type={msg.chat_type}  chat_id={msg.chat_id}")
        print(f"   发送者 open_id = {oid}   {'(= OWNER)' if oid == owner else '(把它填进 FEISHU_OWNER_OPEN_ID)'}")
        print(f"   类型={msg.message_type}  内容={msg.content[:200]}")
        text = f"自检成功 ✅\n你的 open_id：{oid}\n当前会话 chat_id：{msg.chat_id}"
        req = ReplyMessageRequest.builder().message_id(msg.message_id).request_body(
            ReplyMessageRequestBody.builder().msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False)).build()).build()
        resp = cli.im.v1.message.reply(req)
        print("   回复：" + ("✅ 已发送" if resp.success() else f"❌ code={resp.code} msg={resp.msg}（检查是否开通了发消息权限）"))

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()
    print("正在建立长连接……看到 'connected to wss://' 后：")
    print("  1) 回到开发者后台保存「长连接」订阅方式（第 5 步）")
    print("  2) 在飞书里给机器人发任意一句话（第 7 步）。Ctrl+C 退出。")
    lark.ws.Client(env["FEISHU_APP_ID"], env["FEISHU_APP_SECRET"], event_handler=handler,
                   domain=domain(env), log_level=lark.LogLevel.INFO).start()


def cmd_send(env):
    oid = env.get("FEISHU_OWNER_OPEN_ID")
    if not oid:
        sys.exit("先用 listen 拿到 open_id 并填入 FEISHU_OWNER_OPEN_ID")
    req = CreateMessageRequest.builder().receive_id_type("open_id").request_body(
        CreateMessageRequestBody.builder().receive_id(oid).msg_type("text")
        .content(json.dumps({"text": "Personal Agent 主动推送测试 ✅"}, ensure_ascii=False)).build()).build()
    resp = client(env).im.v1.message.create(req)
    print("✅ 已发送，去飞书查看" if resp.success() else f"❌ code={resp.code} msg={resp.msg}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    cmds = {"token": cmd_token, "listen": cmd_listen, "send": cmd_send}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]](load_env())
