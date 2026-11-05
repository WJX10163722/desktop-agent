"""
agent.notify —— 完成通知（增量 C）

场景：让 agent 跑一个长任务，人去干别的；跑完得有个响声，否则回来发现
它十分钟前就结束了、一直在等你。

实现上不引依赖：调用 PowerShell 的 NotifyIcon 弹托盘气泡。
    为什么不用 Toast（WinRT）通知：需要 AppUserModelID，打包后失效概率高；
    托盘气泡在 Win10/11 上稳定，虽然朴素，但"会响"比"好看"重要。

失败必须静默：通知弹不出来是小事，不能让它拖垮任务收尾。
"""
from __future__ import annotations

import subprocess
import sys

_PS_SCRIPT = """
Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.Visible = $true
$n.ShowBalloonTip(6000, '{title}', '{message}', [System.Windows.Forms.ToolTipIcon]::Info)
Start-Sleep -Milliseconds 6000
$n.Dispose()
"""


def _escape(text: str) -> str:
    return text.replace("'", "''")


def notify(title: str, message: str, timeout: int = 10) -> bool:
    """弹一条系统托盘通知。返回是否成功发出（不保证用户看见）。"""
    if sys.platform != "win32":
        return False
    script = _PS_SCRIPT.format(title=_escape(title), message=_escape(message))
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            capture_output=True,
            timeout=timeout,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
