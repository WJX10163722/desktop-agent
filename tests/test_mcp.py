"""
MCP 自测（不需要 Key）

跑法：
    python tests/test_mcp.py

验证的是"外部工具能不能真的接进来"：
    1. 子进程启动 + initialize 握手成功
    2. tools/list 能拿到三个工具和它们的 JSON Schema
    3. tools/call 能真的执行（now / list_dir / file_stats）
    4. 沙箱仍然有效（越界路径被拒）
    5. 注册进 ToolRegistry 后，名字是 mcp__workspace__xxx，模型可调用
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.mcp_client import MCPClient, SERVER_SCRIPT
from agent.tools import build_default_registry

passed = 0
failed = 0


def check(title: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {title}")
    else:
        failed += 1
        print(f"  FAIL  {title} {detail}")


def main() -> None:
    print("\n[1] 启动 MCP server 并握手")
    client = MCPClient(label="workspace", command=sys.executable, args=[str(SERVER_SCRIPT)])
    client.start()

    tools = client.list_tools()
    names = {t["name"] for t in tools}
    check("拿到工具清单", names == {"now", "file_stats", "list_dir"}, names)
    check("工具有描述", all(t["description"] for t in tools))
    check("工具有 JSON Schema", all(t["inputSchema"].get("type") == "object" for t in tools))

    print("\n[2] 调用外部工具")
    text, failed_flag = client.call("now", {})
    check("now 返回时间", not failed_flag and len(text) >= 10 and ":" in text, text)

    text, failed_flag = client.call("list_dir", {"path": "."})
    check("list_dir 列出文件", not failed_flag and "main.py" in text, text[:80])

    text, failed_flag = client.call("file_stats", {"path": "requirements.txt"})
    check("file_stats 返回统计", not failed_flag and "行" in text, text)

    print("\n[3] 错误处理")
    text, failed_flag = client.call("file_stats", {"path": "../../../../Windows/win.ini"})
    check("越界路径被拒绝", failed_flag and "工作目录之外" in text, text)

    text, failed_flag = client.call("不存在的工具", {})
    check("不存在的工具返回错误", failed_flag, text[:80])

    print("\n[4] 注册进 ToolRegistry")
    from agent.mcp_client import attach_mcp_tools

    registry = build_default_registry()
    attached = attach_mcp_tools(registry)
    check("attach 成功", attached is not None)
    mcp_names = [n for n in registry.names if n.startswith("mcp__")]
    check("MCP 工具已注册", len(mcp_names) == 3, mcp_names)
    check("本地四工具仍在", {"read", "write", "edit", "bash"} <= set(registry.names), registry.names)

    result = registry.call("mcp__workspace__file_stats", '{"path": "main.py"}')
    check("通过注册表能调通 MCP 工具", result.ok and "行" in result.content, result.content[:80])
    check("返回的 schema 会喂给模型", any(s["function"]["name"].startswith("mcp__") for s in registry.schemas()))

    if attached:
        attached.stop()
    client.stop()

    print(f"\n结果：{passed} 通过，{failed} 失败")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
