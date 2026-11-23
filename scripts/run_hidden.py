"""在后台无窗口地运行一个命令（供任务计划程序用 pythonw 调用）。

`powershell -WindowStyle Hidden` 仍会先闪出一个控制台窗口并抢走焦点；
这里由 pythonw（GUI 程序，没有控制台）以 CREATE_NO_WINDOW 启动子进程，全程不出现窗口。
子进程的退出码原样返回，任务计划程序里的「上次运行结果」仍然有效。

用法: .venv\\Scripts\\pythonw.exe -I scripts\\run_hidden.py <程序> [参数...]
"""
import subprocess
import sys

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
