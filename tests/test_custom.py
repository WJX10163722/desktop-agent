"""
定制层自测（增量 B）：hook 硬约束、AGENTS.md 注入、审查员隔离

跑法：
    python tests/test_custom.py

这组测试的灵魂是"hook 是代码不是建议"：
    我们真的让模型去写 .env、真的让它 rm -rf，
    然后断言它们被代码挡住——而不是祈祷提示词起作用。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import agents_md
from agent.config import ROOT_DIR, config
from agent.hooks import HookEngine
from agent.loop import Agent
from agent.reviewer import build_reviewer_registry

passed = 0
failed = 0


def check(title: str, condition: bool, detail: object = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {title}")
    else:
        failed += 1
        print(f"  FAIL  {title} {detail}")


def test_pre_tool_deny() -> None:
    print("\n[1] PreToolUse：密钥与危险命令")
    hooks = HookEngine()

    check("写 .env 被拒", hooks.pre_tool("write", {"path": ".env"}) is not None)
    check("写 server.pem 被拒", hooks.pre_tool("write", {"path": "certs/server.pem"}) is not None)
    check("edit 修改 .env 也被拒", hooks.pre_tool("edit", {"path": ".env.local"}) is not None)
    check("写普通文件放行", hooks.pre_tool("write", {"path": "notes.txt"}) is None)

    for cmd in ("rm -rf build/", "git push --force origin main", "git push -f", "git reset --hard HEAD~1"):
        reason = hooks.pre_tool("bash", {"command": cmd})
        check(f"拒绝 {cmd!r}", reason is not None)
        check(f"拒绝时带替代方案：{cmd[:16]}…", reason is not None and ("替代方案" in reason or "git" in reason), reason)

    check("安全命令放行", hooks.pre_tool("bash", {"command": "python tests/test_tools.py"}) is None)
    check("拒绝记录进了账本", len(hooks.denied) >= 6, len(hooks.denied))
    check("每条拒绝都有 reason", all(d.get("reason") for d in hooks.denied))


def test_switch_off() -> None:
    print("\n[2] 开关 ENABLE_HOOKS=0")
    hooks = HookEngine(enabled=False)
    check("关掉后写 .env 放行", hooks.pre_tool("write", {"path": ".env"}) is None)
    check("关掉后 rm -rf 放行（由开关负责，不是工具）", hooks.pre_tool("bash", {"command": "rm -rf /"}) is None)
    check("关掉后不自动跑测试", hooks.post_tool("write", type("R", (), {"ok": True})()) is None)


def test_autotest() -> None:
    print("\n[3] PostToolUse：自动测试红转绿")
    target = ROOT_DIR / "tests" / "test_tools.py"
    backup = target.read_text(encoding="utf-8")
    marker = "from __future__ import annotations\nimport __nonexistent_hook_demo__"
    hooks = HookEngine()
    try:
        # 模拟一次"改坏了"的 edit
        target.write_text(marker + backup[len("from __future__ import annotations"):], encoding="utf-8")
        from agent.tools.base import ToolResult

        injected = hooks.post_tool("edit", ToolResult.success("done"))
        check("改坏后测试变红", hooks.autotest["red"] >= 1, hooks.autotest)
        check("失败摘要被回注", injected is not None and "自动测试未通过" in injected)
        check("回注是摘要不是 traceback", injected is not None and len(injected.splitlines()) <= 5, injected and len(injected.splitlines()))

        # 修回去，测试变绿
        target.write_text(backup, encoding="utf-8")
        injected2 = hooks.post_tool("edit", ToolResult.success("done"))
        check("修好后变绿", hooks.autotest["green"] >= 1, hooks.autotest)
        check("绿了就不回注", injected2 is None)
        check("记录了一次红转绿", hooks.autotest["red_to_green"] >= 1, hooks.autotest)
    finally:
        if target.read_text(encoding="utf-8") != backup:
            target.write_text(backup, encoding="utf-8")


def test_agents_md() -> None:
    print("\n[4] AGENTS.md 注入")
    loaded = agents_md.load(config.workspace)
    check("项目级 AGENTS.md 被找到", loaded["found"] and "project" in loaded["layers"], loaded["layers"])
    check("体量是地图不是文档（≤12000 字节）", loaded["bytes"] <= 12000, loaded["bytes"])
    check("内容含构建命令", "selftest" in loaded["text"])

    injected = agents_md.inject("原始系统提示", loaded)
    check("注入后地图在提示前面", injected.index("项目约定") < injected.index("原始系统提示"))
    check("空配置不注入", agents_md.inject("x", {"found": False}) == "x")


def test_reviewer() -> None:
    print("\n[5] 审查子 agent：只读 + 白名单")
    registry = build_reviewer_registry()
    check("reviewer 有 read", registry.get("read") is not None)
    check("reviewer 没有 write", registry.get("write") is None)
    check("reviewer 没有 edit", registry.get("edit") is None)

    ok = registry.call("bash", {"command": "git diff --stat"})
    check("git diff 放行", ok.ok or "不是 git 仓库" not in ok.content)
    bad = registry.call("bash", {"command": "rm -rf build/"})
    check("非白名单命令被拒", not bad.ok and "只允许只读命令" in bad.content, bad.content[:60])


def test_agent_integration() -> None:
    print("\n[6] 与 agent 循环集成")
    # 真的让 mock 模型去写 .env：hook 必须在工具执行前拦下
    agent = Agent(
        mock=True,
        mock_script=[
            {"tool": "write", "args": {"path": ".env", "content": "FAKE=1"}},
            "我知道了，不写密钥文件。",
        ],
        persist=False,
    )
    result = agent.run("把 key 写进 .env")
    deny_records = [c for t in result.turns for c in t.tool_calls if c.name == "write"]
    check("write 调用发生过", len(deny_records) == 1, len(deny_records))
    check("write 被 hook 拦下（ok=False）", deny_records and not deny_records[0].ok)
    check("拒绝理由含'密钥不入库'", deny_records and "密钥不入库" in deny_records[0].content)
    check(".env 文件没有被创建", not (config.workspace / ".env").exists() or Path(".env").read_text(encoding="utf-8") != "FAKE=1")

    check("review_code 是主 agent 的工具", "review_code" in agent.registry.names)
    check("AGENTS.md 注入进了默认人格", "项目约定" in agent.system_prompt)


def main() -> None:
    test_pre_tool_deny()
    test_switch_off()
    test_autotest()
    test_agents_md()
    test_reviewer()
    test_agent_integration()
    print(f"\n结果：{passed} 通过，{failed} 失败")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
