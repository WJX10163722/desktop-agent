"""
agent.mcp_client —— MCP 客户端：把外部 MCP server 的工具接进我们的注册表

难点只有一个：**异步 vs 同步**。
    MCP 官方 SDK 是全异步的（asyncio），而我们的 agent 循环是同步的（线程里跑）。
    解法：给 MCP 客户端开一个专属的后台事件循环线程，
         同步代码用 run_coroutine_threadsafe 把协程丢进去执行并等结果。

接进来之后，模型看到的工具长这样：
    mcp__workspace__now        （外部 server 提供的"当前时间"）
    mcp__workspace__file_stats （外部 server 提供的"文件统计"）
它和本地 read/write/edit/bash 在注册表里平起平坐，模型感知不到区别 —— 这就是 MCP 的意义。
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path
from typing import Any

from .config import config
from .tools import Tool, ToolRegistry, ToolResult

ROOT_DIR = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = ROOT_DIR / "mcp_servers" / "workspace_server.py"


def _explain(err: BaseException) -> str:
    """把嵌套的 ExceptionGroup 拆成一句人话。

    anyio 的报错长这样：ExceptionGroup(unhandled errors) -> ExceptionGroup -> 真实异常
    不拆开的话，用户看到的永远是"unhandled errors in a TaskGroup"这种废话。
    """
    leaves: list[str] = []

    def walk(e: BaseException) -> None:
        subs = getattr(e, "exceptions", None)
        if subs:
            for sub in subs:
                walk(sub)
        else:
            leaves.append(f"{type(e).__name__}: {e}")

    walk(err)
    # 去重但保持顺序
    return "；".join(dict.fromkeys(leaves)) or str(err)


class MCPClient:
    """一个 MCP server 连接（stdio 传输，子进程）。"""

    def __init__(self, label: str = "workspace", command: str | None = None,
                 args: list[str] | None = None, timeout: float = 40.0) -> None:
        self.label = label
        self.command, self.args = command, args or []
        self.timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Any = None
        self._tools: list[dict] = []
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._error: str | None = None

    # ---------------- 生命周期 ----------------
    def start(self) -> "MCPClient":
        """启动子进程并完成 initialize + tools/list（阻塞到就绪）。"""
        self._thread = threading.Thread(target=self._thread_main, name=f"mcp-{self.label}", daemon=True)
        self._thread.start()
        if not self._ready.wait(self.timeout):
            raise RuntimeError(f"MCP server `{self.label}` 在 {self.timeout:.0f} 秒内没有就绪。{self._error or ''}")
        if self._error:
            raise RuntimeError(self._error)
        return self

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except BaseException as e:  # noqa: BLE001
            # anyio 的 TaskGroup 会把真实错误层层包在 ExceptionGroup 里，
            # 必须递归拆到底层，否则只能看到一句没用的 "unhandled errors"
            self._error = f"MCP 连接失败：{_explain(e)}"
            self._ready.set()
        finally:
            try:
                self._loop.close()
            except Exception:  # noqa: BLE001
                pass

    async def _serve(self) -> None:
        from mcp import Client, StdioServerParameters

        # 注意：env 必须继承父进程环境。
        # Windows 上只传几个自定义变量，子进程会缺 SYSTEMROOT / PATH，
        # python 连启动都启动不了（初始化文件系统编码失败）。
        child_env = {**os.environ, "MCP_WORKSPACE": str(config.workspace), "PYTHONUTF8": "1"}

        # 打包成 exe 后没有 python 解释器，就让 exe 自己带 --mcp-stdio 参数充当 server
        params = StdioServerParameters(
            command=self.command or sys.executable,
            args=self.args,
            env=child_env,
            cwd=str(config.workspace),
            encoding="utf-8",
            encoding_error_handler="replace",
        )

        self._stop = asyncio.Event()
        async with Client(params) as client:
            self._client = client
            result = await client.list_tools()
            # 注意：MCP SDK 2.x 的字段是蛇形 input_schema（1.x 是 inputSchema）
            self._tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": getattr(t, "input_schema", None) or {"type": "object", "properties": {}},
                }
                for t in result.tools
            ]
            self._ready.set()
            await self._stop.wait()  # 保持连接，直到 stop()

    def stop(self) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(timeout=5)

    # ---------------- 工具 ----------------
    @property
    def tools(self) -> list[dict]:
        return self._tools

    def list_tools(self) -> list[dict]:
        return self._tools

    def call(self, name: str, arguments: dict) -> tuple[str, bool]:
        """同步调用一个 MCP 工具，返回 (文本结果, 是否失败)。"""
        if not self._loop or not self._client:
            return "MCP 连接未就绪。", True
        fut = asyncio.run_coroutine_threadsafe(
            self._client.call_tool(name, arguments or {}), self._loop
        )
        try:
            result = fut.result(timeout=self.timeout)
        except Exception as e:  # noqa: BLE001
            return f"MCP 工具调用失败：{type(e).__name__}: {e}", True

        texts = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                texts.append(text)
        body = "\n".join(texts) or "（工具没有返回文本内容）"

        # SDK 2.x 用蛇形 is_error（1.x 是 isError），两个都兼容
        failed = bool(getattr(result, "is_error", None) or getattr(result, "isError", False))
        if body.startswith("ERROR:"):
            failed = True
            body = body[len("ERROR:"):].strip()
        return body, failed


def attach_mcp_tools(registry: ToolRegistry, label: str = "workspace") -> MCPClient | None:
    """启动 MCP server，把它暴露的每个工具注册进 registry。

    任何一步失败都不许影响主程序：MCP 是加分项，不是命根子。
    """
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        command, args = sys.executable, ["--mcp-stdio"]
    else:
        command, args = sys.executable, [str(SERVER_SCRIPT)]

    client = MCPClient(label=label, command=command, args=args)
    try:
        client.start()
    except Exception as e:  # noqa: BLE001
        print(f"[MCP] 跳过外部工具（{e}）")
        return None

    for meta in client.list_tools():
        raw_name = meta["name"]
        tool_name = f"mcp__{label}__{raw_name}"
        if registry.get(tool_name) is not None:
            continue

        def make_fn(mcp_name: str):
            def _call(**kwargs: Any) -> ToolResult:
                text, failed = client.call(mcp_name, kwargs)
                return ToolResult.failure(text) if failed else ToolResult.success(text)
            return _call

        registry.register(
            Tool(
                name=tool_name,
                description=(
                    f"[MCP:{label}] {meta['description']}\n"
                    f"（此工具由外部 MCP server `{label}` 提供，通过标准 MCP 协议调用。）"
                ),
                parameters=meta["inputSchema"],
                func=make_fn(raw_name),
            )
        )
    return client
