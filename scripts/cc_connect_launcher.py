"""后台启动 cc-connect（供任务计划程序用 pythonw 调用，全程无控制台窗口）。

从 .env 读取 FEISHU_* 变量，只注入子进程环境；cc-connect 输出写到 logs/cc-connect.log。
cc-connect 退出时本进程以相同退出码退出，任务计划程序据此自动重启。
用法: .venv\\Scripts\\pythonw.exe -I scripts\\cc_connect_launcher.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "tools" / "cc-connect" / "cc-connect.exe"
CONFIG = ROOT / "tools" / "cc-connect" / "config.toml"
LOG = ROOT / "logs" / "cc-connect.log"


def kill_on_close_job():
    """创建「句柄关闭即结束全部进程」的 Job Object。

    Stop-ScheduledTask 只会结束本启动器；把 cc-connect 放进这个 job 后，启动器一退出，
    job 句柄随之关闭，cc-connect 及其派生的 claude 进程会被一并结束，不会留下孤儿进程
    继续占着实例锁。
    """
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # ExtendedLimitInformation
        raise ctypes.WinError(ctypes.get_last_error())
    return k32, job


def main() -> int:
    env = os.environ.copy()
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(FEISHU_[A-Z_]+)\s*=\s*(.*?)\s*$", line)
        if m:
            env[m.group(1)] = m.group(2)
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "tools" / "cc-connect" / "data").mkdir(exist_ok=True)
    k32, job = kill_on_close_job()
    with open(LOG, "ab") as log:
        p = subprocess.Popen([str(EXE), "--config", str(CONFIG)], cwd=ROOT, env=env,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        if not k32.AssignProcessToJobObject(job, int(p._handle)):
            p.kill()
            raise OSError("AssignProcessToJobObject failed")
        return p.wait()


if __name__ == "__main__":
    sys.exit(main())
