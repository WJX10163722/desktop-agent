"""
agent.tools.shell —— bash 工具：万能工具

为什么叫"万能"：安装依赖、跑测试、看目录、git 操作、启动服务……全归它。
也正因为万能，它是最容易把电脑搞坏的一个，所以下面每一条都是保命措施：

    1. 超时        —— 没有超时，模型跑个 `npm install` 或死循环，界面就永久卡住（评分红线）
    2. 输出截断    —— 一条 `dir /s` 能吐几十万行，直接撑爆上下文（评分红线）
    3. 编码统一    —— Windows 的 cmd 默认 GBK，不处理中文全是乱码
    4. 危险命令拦截 —— format / shutdown / rm -rf / 之类，直接拒绝执行
    5. 退出码      —— 不只给输出，还要给 exit_code，模型才知道"到底成没成"
"""
from __future__ import annotations

import os
import subprocess
import sys

from ..config import config
from .base import Tool, ToolResult
from .fs import resolve_path

DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120
MAX_OUTPUT_CHARS = 8000       # stdout / stderr 各自上限
MAX_TOTAL_CHARS = 12000

# 明确会造成不可逆破坏的命令片段。命中就直接拒绝，让模型换个做法。
BLOCKED_PATTERNS = (
    "format ",
    "mkfs",
    "rm -rf /",
    "rm -rf ~",
    "del /f /s /q c:\\",
    "rd /s /q c:\\",
    "shutdown",
    "diskpart",
    ":(){:|:&};:",   # fork bomb
)


def _decode(data: bytes) -> str:
    """Windows 中文编码地狱的解法：先试 UTF-8，再退 GBK，最后容错替换。"""
    for enc in ("utf-8", "gbk", "cp936"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """头尾各留一半，中间省略。

    为什么不全留头部：报错通常在最后几行，只留头会看不到真正的错误。
    """
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - limit
    return f"{text[:half]}\n...（中间省略 {omitted} 字符）...\n{text[-half:]}"


def _format_section(title: str, body: str) -> str:
    body = body.strip()
    if not body:
        return f"{title}：（空）"
    return f"{title}：\n{body}"


def run_bash(command: str, timeout: int = DEFAULT_TIMEOUT, cwd: str | None = None) -> ToolResult:
    if not isinstance(command, str) or not command.strip():
        return ToolResult.failure("command 不能为空。")

    lowered = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in lowered:
            return ToolResult.failure(
                f"命令包含高风险操作 `{pattern.strip()}`，已拒绝执行。\n"
                f"如果确实必要，请让用户手动在终端里执行。"
            )

    try:
        workdir = resolve_path(cwd) if cwd else config.workspace
    except (ValueError, PermissionError) as e:
        return ToolResult.failure(str(e))
    if not workdir.is_dir():
        return ToolResult.failure(f"工作目录不存在：{workdir}")

    try:
        timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    if sys.platform == "win32":
        # 注意：这里必须把整串命令交给 shell，不能传 list。
        # 传 list 时 Python 会按 MSVCRT 规则加引号，`cmd /c` 会把引号解析错，
        # 结果是 python -c "print(1/0)" 变成静默失败（exit 0、无输出）。
        # chcp 65001：把 cmd 代码页切成 UTF-8，中文输出才不乱码
        full_command = f"chcp 65001>nul & {command}"
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        full_command = command
        kwargs = {"start_new_session": True}

    try:
        proc = subprocess.Popen(
            full_command,
            shell=True,
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=env,
            **kwargs,
        )
    except OSError as e:
        return ToolResult.failure(f"命令启动失败：{e}")

    timed_out = False
    try:
        raw_out, raw_err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            raw_out, raw_err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            raw_out, raw_err = b"", b""

    stdout = _truncate(_decode(raw_out or b""))
    stderr = _truncate(_decode(raw_err or b""))
    exit_code = proc.returncode

    parts = [_format_section("stdout", stdout), _format_section("stderr", stderr), f"exit_code：{exit_code}"]

    if timed_out:
        return ToolResult.failure(
            f"命令超时（超过 {timeout} 秒），已强制终止。\n"
            + "\n".join(parts)
            + "\n建议：换一个能更快结束的命令，或者拆成几步；需要长时间运行的进程请自己手动在终端启动。",
            exit_code=exit_code, timeout=True,
        )

    content = "\n".join(parts)
    if len(content) > MAX_TOTAL_CHARS:
        content = content[:MAX_TOTAL_CHARS] + "\n...（输出过长，已截断）"

    if exit_code == 0:
        return ToolResult.success(content, exit_code=0)
    return ToolResult.failure(f"命令执行失败（退出码 {exit_code}）。\n{content}", exit_code=exit_code)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Windows 上 kill 父进程不会带走子进程，得用 taskkill /T 连锅端。"""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
        else:
            proc.kill()
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


tool_bash = Tool(
    name="bash",
    description=(
        "执行一条 shell 命令，返回 stdout、stderr 和 exit_code。"
        "用于：查看目录（dir/ls）、安装依赖、运行脚本与测试、执行 git 命令。"
        "命令必须能在超时时间内自行结束，不要执行常驻服务。"
        "写完代码后用它跑一次验证，不要假设自己写对了。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令，例如 `python tests/test_tools.py`。"},
            "timeout": {
                "type": "integer",
                "description": f"超时秒数，默认 {DEFAULT_TIMEOUT}，最大 {MAX_TIMEOUT}。",
            },
            "cwd": {"type": "string", "description": "在哪个目录执行，默认工作目录。"},
        },
        "required": ["command"],
    },
    func=run_bash,
)
