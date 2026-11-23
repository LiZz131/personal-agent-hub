"""常驻：每台远程服务器保持一条 SSH 长连接，远程一有 Claude Code 事件就推送过来（约 1 秒）。

取代「每分钟 SSH 拉一次」：远程连不到本机（本机在 NAT 后面），所以由本机发起一条长连接；
远程侧运行一个仅存在于内存中的小监视器（通过 python3 -c 传过去，不在服务器上安装任何东西），
发现 ~/.hub-outbox/*.json 就打印出来并移到 sent/。连接断开后自动重连（退避 5→60 秒）。
本机侧收到事件后交给 hub_notify.deliver：记入 events/log.jsonl 并推送到飞书。

由任务计划程序在登录时启动（pythonw，无窗口，崩溃自动重启）。
手动运行：.venv\\Scripts\\python -I scripts\\remote_event_stream.py
"""
from __future__ import annotations

import base64
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hub_notify import ROOT, deliver  # noqa: E402
from pull_remote_events import remotes  # noqa: E402

LOG = ROOT / "events" / "stream.log"
SINGLE_INSTANCE_PORT = 47322

# 远程监视器：每秒查一次本地发件箱（纯文件系统，不产生网络流量），每 30 秒发一次心跳；
# 连接断开时 print 会失败，进程随之退出，不会在服务器上残留。
WATCHER = r'''
import glob, json, os, sys, time
d = os.path.expanduser(sys.argv[1]); sent = os.path.join(d, "sent")
os.makedirs(sent, exist_ok=True)
last_hb = last_prune = 0
while True:
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            s = open(f, encoding="utf-8").read().strip()
        except OSError:
            continue
        if s:
            print(s, flush=True)
        os.replace(f, os.path.join(sent, os.path.basename(f)))
    now = time.time()
    if now - last_hb > 30:
        print('{"hb": 1}', flush=True); last_hb = now
    if now - last_prune > 3600:
        for f in glob.glob(os.path.join(sent, "*.json")):
            if now - os.path.getmtime(f) > 7 * 86400:
                os.remove(f)
        last_prune = now
    time.sleep(1)
'''


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


ARTIFACTS = ROOT / "events" / "remote_artifacts"
ARTIFACT_LIMIT = 30 * 1024 * 1024


def fetch_artifacts(remote: dict, ev: dict) -> None:
    """远程检查点的产物：用 scp 取回本机（每个文件一次连接，假设服务器没有 SSH 连接频率限制），路径改成本机路径。
    取不回的保留远程路径，并在说明里注明。"""
    dest = ARTIFACTS / ev["id"]
    dest.mkdir(parents=True, exist_ok=True)
    local, notes = [], []
    for path in ev["artifacts"]:
        target = dest / Path(path).name
        p = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", f"{remote['ssh']}:{path}", str(target)],
                           capture_output=True, timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.returncode == 0 and target.exists() and target.stat().st_size <= ARTIFACT_LIMIT:
            local.append(str(target))
        else:
            notes.append(f"- 未取回（{'超过 30 MB' if target.exists() else 'scp 失败'}）：{remote['ssh']}:{path}")
            target.unlink(missing_ok=True)
    ev["remote_artifacts"] = ev["artifacts"]
    ev["artifacts"] = local
    if notes:
        ev["summary"] = (ev.get("summary") or "") + "\n\n" + "\n".join(notes)


def stream(remote: dict) -> None:
    code = base64.b64encode(WATCHER.encode()).decode()
    remote_cmd = f"python3 -u -c \"import base64;exec(base64.b64decode('{code}'))\" {remote['dir']}"
    backoff = [5, 15, 30, 60]
    failures = 0
    while True:
        started = time.time()
        log(f"{remote['ssh']}: connecting")
        p = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                              "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", remote["ssh"], remote_cmd],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for raw in p.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("hb"):
                continue
            ev.setdefault("project", remote["project"])
            ev.setdefault("host", remote["ssh"])
            try:
                if ev.get("kind") == "checkpoint" and ev.get("artifacts"):
                    fetch_artifacts(remote, ev)
                deliver(ev)
                log(f"{remote['ssh']}: delivered {ev.get('kind')} {ev.get('id')}")
            except Exception as e:  # noqa: BLE001
                log(f"{remote['ssh']}: deliver failed {ev.get('id')}: {e}")
                import incident
                incident.report_exc("remote_event_stream", log="events/stream.log")
        p.wait()
        err = p.stderr.read().decode("utf-8", errors="replace").strip()[-300:]
        log(f"{remote['ssh']}: disconnected (exit {p.returncode}) {err}")
        failures = 0 if time.time() - started > 300 else failures + 1
        time.sleep(backoff[min(failures, len(backoff) - 1)])


def main():
    guard = socket.socket()
    try:
        guard.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        log("another remote_event_stream instance is running; exit")
        return
    threads = [threading.Thread(target=stream, args=(r,), daemon=True) for r in remotes()]
    for t in threads:
        t.start()
    log(f"started for {[r['ssh'] for r in remotes()]}")
    while any(t.is_alive() for t in threads):
        time.sleep(60)


if __name__ == "__main__":
    main()
