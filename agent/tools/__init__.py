"""
agent.tools —— 工具层

目录说明：
    base.py  工具的"协议"：Tool / ToolResult / ToolRegistry
    fs.py    文件类工具：read / write（edit 下一步加）
    shell.py 命令类工具：bash（下一步加）

为什么工具要"注册"而不是 if-else：
    评分表里"代码结构"看的就是这一点。加第五个工具时你只改一个文件，
    loop.py 一个字不动 —— 这叫"改起来不痛"。
"""
from .base import Tool, ToolRegistry, ToolResult
from .fs import tool_read, tool_write


def build_default_registry(workspace: str | None = None) -> ToolRegistry:
    """建一个装好全部工具的注册表。

    每个新工具只需要在这里 register 一行，循环那边自动就能用。
    """
    registry = ToolRegistry(workspace=workspace)
    for tool in (tool_read, tool_write):
        registry.register(tool)
    return registry


__all__ = ["Tool", "ToolResult", "ToolRegistry", "build_default_registry", "tool_read", "tool_write"]
