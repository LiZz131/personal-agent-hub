# 「项目通知」群：操作文档

目标：本地和远程所有 Claude Code 会话的关键事件（**需要审批 / 等你回复 / 长任务完成**），都及时出现在飞书群里。

这些通知由 **Personal Agent 机器人**发出，事件本身先记录在 hub，所以 agent 知道发生了什么。你可以直接在群里 @Personal Agent 追问或处理。

整体结构和设计理由见 `docs/design/supervisor.md`。

---

## 第 1 步：建群

1. 飞书客户端 → 右上角 **「+」→「创建群组」**。
2. 群名称：`项目通知`。以后要按项目分群的话，可以建 `📦 项目 A`、`📦 项目 B`。
3. 成员**只选你自己**，创建。

> 先建一个共用的「项目通知」群就够了。等消息多了、想按项目隔离时，再按项目建群，在 `.env` 的 `FEISHU_PROJECT_CHAT_IDS` 里配置。

## 第 2 步：把 Personal Agent 机器人拉进群

1. 打开群 → 右上角 **「…」→「设置」→「群机器人」→「添加机器人」**。
2. 搜索 **Personal Agent**（你的自建应用）→ 添加。
   - 搜不到时，检查开放平台里这个应用的**可用范围**是否包含你自己，并确认已经发布。
3. 添加成功后，群里会出现系统提示「Personal Agent 加入群聊」。

权限说明：机器人在群里发消息需要 `im:message:send_as_bot`；你在群里 @它，它要收到消息需要 `im:message.group_at_msg:readonly`。这两项在 `docs/feishu-setup.md` 第 4 步都已经开通，**不需要再改权限**。

默认情况下，机器人在群里只响应 **@它**的消息，不会对群里每条消息都回复。

## 第 3 步：拿到群的 chat_id

最简单的方法是让 cc-connect 的日志记录下来：

1. 在群里发：`@Personal Agent /whoami`
2. 机器人会回复你的用户 ID。与此同时，`<hub>\logs\cc-connect.log` 会记下一行，里面有 `session_key=feishu:oc_xxxxxxxx:ou_…`，**`oc_` 开头的那一段就是群的 chat_id**。
3. 把它填进 `.env`：
   ```
   FEISHU_NOTIFY_CHAT_ID=oc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   也可以直接在飞书里告诉 agent「帮我读出项目通知群的 chat_id」，它会从日志里找出来告诉你，但不会替你写进 `.env`。

如果按项目分群，每个群重复第 2、3 步，然后填：
```
FEISHU_PROJECT_CHAT_IDS=project-a=oc_aaa,project-b=oc_bbb
```
项目名要和 `projects/*.md` 的文件名一致。

## 第 4 步（可选）：webhook 兜底机器人

正常情况下**不需要**这一步。它只在 hub 不可用时（比如本机关机），让远程的「需要审批」类事件还能直接告警。

1. 在同一个群：**「设置」→「群机器人」→「添加机器人」→「自定义机器人」**。
2. 名称：`项目通知（兜底）`。描述随意。
3. **安全设置**：勾选 **「签名校验」**。不要选「自定义关键词」或「IP 白名单」：远程服务器的出口 IP 可能会变，关键词也容易误拦。
4. 复制 **webhook 地址**（形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxx`）和**签名密钥**，填进 `.env`：
   ```
   FEISHU_NOTIFY_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxx
   FEISHU_NOTIFY_WEBHOOK_SECRET=xxxxxxxx
   ```
5. 这对地址和密钥以后会**部署到远程服务器**上，由 agent 来做，存在服务器的私有配置文件里，权限是 600。
   - 泄露的后果：别人只能往这个群发消息，读不到任何内容。
   - 处理办法：在群设置里删掉这个机器人，重建一个，就能让旧地址失效。

> ⚠️ webhook 机器人发出的消息 **Personal Agent 看不到**：飞书不会把一个机器人发的消息推送给另一个机器人。所以它只适合做兜底告警，不能替代第 2 步。

## 第 5 步：填好 .env 后告诉 agent

需要填的只有 `FEISHU_NOTIFY_CHAT_ID`；如果做了第 4 步，再加上那两项 webhook 配置。agent 接下来会：
1. 在本机装通知 hook，覆盖所有本地项目；
2. 在远程项目的服务器上装远程 hook 和发件箱（`scripts/deploy_remote.py`）；
3. 发一条测试通知，以及模拟一次「需要审批」，确认群里能收到，并确认 agent 能查到这个事件。

---

## 通知规则（实施后的行为）

| 事件 | 触发 | 是否推送 |
|---|---|---|
| ⚠️ 需要审批 | Claude Code 弹出权限确认 | **立即推送** |
| ❓ 等你回复 | Claude 停下来等你回复，空闲约 60 秒 | **立即推送** |
| ✅ 长任务完成 | 一轮结束，且距离你上次发消息超过 `NOTIFY_LONG_TASK_MINUTES` 分钟 | 推送 |
| 普通一问一答 | 你在电脑前和 Claude 来回对话 | **不推送** |
| 无头实验运行（`claude -p`），以及由 cc-connect 启动的会话 | — | **不推送**：前者是批量实验，后者本来就在飞书里回复 |

同一会话、同一类通知，`NOTIFY_DEDUP_SECONDS` 秒内只推一次。
