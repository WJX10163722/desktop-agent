"""
mcp_servers.workspace_server —— 一个独立的 MCP server（stdio 传输）

它和我们自己的 agent 是**两个进程**：
    主程序 ──启动子进程──> 这个文件
    主程序 ──JSON-RPC(stdin/stdout)──> 这个文件里的工具

为什么要单独搞一个：
    MCP（Model Context Protocol）的价值就是"工具可以来自任何地方"——
    别人写好的 GitHub server、数据库 server，只要说 MCP，你的 agent 就能接。
    我们这个 server 相当于"自己写了一个可以被任何 MCP 客户端接的工具箱"。

协议四步（现场演示可以照着说）：
    1. initialize  握手，交换能力
    2. tools/list  拉取工具清单（名称、描述、JSON Schema）
    3. tools/call  调用某个工具，拿回结果
    4. 结束后关闭子进程

跑法（手动调试用）：
    python -m mcp_servers.workspace_server
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from mcp.server.mcpserver import MCPServer

WORKSPACE = Path(os.getenv("MCP_WORKSPACE", os.getcwd())).resolve()

server = MCPServer(
    name="workspace-utils",
    version="1.0.0",
    instructions="提供工作区相关的小工具：当前时间、文件统计、目录列举。",
)


def _safe(path: str) -> Path:
    """和主程序一样的沙箱：不许跑出工作目录。"""
    p = Path(path or ".").expanduser()
    if not p.is_absolute():
        p = WORKSPACE / p
    p = p.resolve()
    if p != WORKSPACE and WORKSPACE not in p.parents:
        raise ValueError(f"路径 `{path}` 在工作目录之外，已拒绝。工作目录：{WORKSPACE}")
    return p


@server.tool(
    name="now",
    description="返回当前本地日期时间（格式 2026-11-03 15:20:31）。需要知道“现在几点/今天几号”时用它，不要自己猜。",
)
def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@server.tool(
    name="file_stats",
    description="统计工作目录中某个文件的行数、字符数、字节数。path 为相对工作目录的路径，例如 main.py。",
)
def file_stats(path: str) -> str:
    # 故意不抛异常：抛异常会被协议包装成 "Error executing tool"，
    # 调用方就看不到"到底为什么失败"。返回 ERROR: 前缀才便于模型纠正。
    try:
        target = _safe(path)
    except ValueError as e:
        return f"ERROR: {e}"
    if not target.exists():
        return f"ERROR: 文件不存在：{target}"
    if target.is_dir():
        return f"ERROR: `{path}` 是目录，不是文件。"
    data = target.read_bytes()
    text = data.decode("utf-8", errors="replace")
    return (
        f"{path}：{len(text.splitlines())} 行，"
        f"{len(text)} 字符，{len(data)} 字节"
    )


@server.tool(
    name="list_dir",
    description="列出工作目录中某个目录下的文件与子目录（不递归），用来了解项目结构。path 默认当前目录。",
)
def list_dir(path: str = ".") -> str:
    try:
        target = _safe(path)
    except ValueError as e:
        return f"ERROR: {e}"
    if not target.exists():
        return f"ERROR: 目录不存在：{target}"
    if not target.is_dir():
        return f"ERROR: `{path}` 是文件，不是目录。"
    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    lines = [f"{'[目录]' if e.is_dir() else '[文件]'} {e.name}" for e in entries]
    return "\n".join(lines) if lines else "（空目录）"


def main() -> None:
    # stdio 传输：从 stdin 收 JSON-RPC，往 stdout 写 JSON-RPC
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
