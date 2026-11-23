# cc-connect 安装与接入

cc-connect 负责飞书对话层：流式进度卡片、会话切换、切模型、切项目。它在 hub 目录（下文记作 `<hub>`）里启动 Claude Code，所以 hub 的 `CLAUDE.md`、skills 和权限白名单全部照常生效。

## 用哪个版本

本项目依赖 cc-connect 的 **fork：<https://github.com/LiZz131/cc-connect>，`hub` 分支**（基于上游 [chenhg5/cc-connect](https://github.com/chenhg5/cc-connect) v1.5.3）。hub 分支相对上游的改动是本项目要用到的：

- 配置里的自定义命令（`[[commands]]`）出现在 `/help` 卡片的「自定义命令」分组；
- `/usage` 在 Windows 上可用；
- 其他与 hub 配合的修复（以 fork 的提交记录为准）。

上游版本大体也能跑，但 `tools/cc-connect/config.toml.example` 里的部分配置项可能不被识别。本仓库**不附带二进制**。

## 安装后的文件位置

| 内容 | 位置 |
|---|---|
| 程序 | `<hub>\tools\cc-connect\cc-connect.exe` |
| 配置 | `<hub>\tools\cc-connect\config.toml`（由 `scripts\install.ps1` 从 `config.toml.example` 渲染，不含明文密钥） |
| 会话数据 | `<hub>\tools\cc-connect\data\`（默认会放在 `C:\Users\<you>\.cc-connect`，这里改到了仓库内） |
| 日志 | `<hub>\logs\cc-connect.log` |
| 密钥 | `<hub>\.env`，由 `scripts\start-cc-connect.ps1` 注入进程环境变量，不写入系统环境变量 |

不要用 `cc-connect daemon install`：它在 Windows 上的行为没有验证过，开机自启改用任务计划程序（第 4 步）。

---

## 第 1 步：获取程序

**从源码编译（推荐）**，需要 Go：

```powershell
cd <hub>
git clone -b hub https://github.com/LiZz131/cc-connect vendor\cc-connect
powershell -ExecutionPolicy Bypass -File scripts\build-cc-connect.ps1     # 输出 tools\cc-connect\cc-connect.new.exe
Move-Item tools\cc-connect\cc-connect.new.exe tools\cc-connect\cc-connect.exe
tools\cc-connect\cc-connect.exe --version
```

`vendor/` 在 `.gitignore` 里。以后更新：`git -C vendor\cc-connect pull`，再运行 `build-cc-connect.ps1 -Install`（编译后停服务、替换 exe、再启动）。

如果 fork 发布了 Windows release，也可以直接下载 `cc-connect.exe` 放到 `tools\cc-connect\`，并核对 release 页面给出的 SHA256。

## 第 2 步：飞书后台补一个权限

先按 `docs/feishu-setup.md` 建好飞书自建应用。cc-connect 还需要读取用户基本信息，在开放平台 → 你的应用 →「权限管理」中开通：

| 权限标识 | 用途 |
|---|---|
| `contact:user.base:readonly` | 获取用户信息，`/whoami` 等功能要用 |

开通后去「版本管理与发布」创建新版本并发布。事件 `im.message.receive_v1` 和回调 `card.action.trigger` 在 `feishu-setup.md` 里已经配好。

> 同一时间只能有一个程序以长连接接入这个应用。用 cc-connect 时不要再运行 `scripts/feishu_bot.py`。

## 第 3 步：前台试运行

```powershell
cd <hub>
powershell -ExecutionPolicy Bypass -File scripts\start-cc-connect.ps1
```

日志里出现飞书连接成功的信息后，在飞书里给机器人依次发：

| 发送 | 预期 |
|---|---|
| `/whoami` | 返回你的 open_id，应该和 `.env` 里的 `FEISHU_OWNER_OPEN_ID` 一致 |
| `/status` | 显示项目 `hub`，工作目录是 `<hub>` |
| 一个关于 wiki 内容的问题 | 一张进度卡片持续刷新，显示读文件等工具调用，最后给出回答 |
| `/list`、`/new 测试`、`/switch <编号>` | 列出、新建、切换会话 |
| `/model` | 列出可切换的模型 |
| 一个博客链接 | 触发入库流程（ingest） |

测完按 `Ctrl+C` 退出。

## 第 4 步：开机自启

```powershell
cd <hub>
powershell -ExecutionPolicy Bypass -File scripts\register-tasks.ps1
Start-ScheduledTask -TaskPath '\PersonalAgent\' -TaskName cc-connect
```

这个脚本会注册 `\PersonalAgent\` 下的计划任务：`cc-connect`（登录后启动，隐藏窗口，崩溃 1 分钟后自动重启）、远程事件流，以及各个定时任务（它们跑完后通过 `feishu_notify.py` 推送结果，只发消息，不和 cc-connect 抢连接）。

## 第 5 步：确认数据没有写到用户目录

```powershell
Test-Path $env:USERPROFILE\.cc-connect     # 预期 False
Get-ChildItem <hub>\tools\cc-connect\data  # 预期有会话数据
```

---

## 安全设置（已写在 config.toml.example）

- `allow_from` 只包含你的 open_id，其他人发消息一律忽略。默认值 `"*"` 表示所有人都能用，**不要删掉这一行**。
- `admin_from` 也只有你，所以只有你能用 `/dir` 切换工作目录。
- 禁用了 `/shell`、`/upgrade`、`/restart`：不允许从飞书直接执行命令，也不允许程序自己升级。
- 权限模式是 `auto`：由 Claude Code 的 auto 模式分类器判断，常规操作直接执行，高风险操作在飞书里弹确认；`.claude/settings.json` 的 deny / ask 规则仍然优先生效。不使用 bypass 模式。

## 常用指令

```
/new [名字]    新会话            /list       会话列表
/switch <id>   切换会话          /current    当前会话
/model         查看或切换模型    /dir <路径>  切换到其他项目目录
/status        状态              /help       全部指令
```

空闲不会自动开新会话（`reset_on_idle_mins = 0`）。要换话题时自己发 `/new`；旧会话不会删除，可以用 `/list` 和 `/switch` 找回来。
