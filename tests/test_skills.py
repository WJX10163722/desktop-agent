"""
技能系统自测：加载、渐进披露、三种形态、开关

跑法：
    python tests/test_skills.py

重点测"披露比"的分子分母是分开的：
    目录进上下文 ≠ 正文进上下文。
    如果有人图省事把 SKILL.md 全拼进 system prompt，
    这里的测试会第一时间红给他看。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import skills as skills_mod
from agent.config import config
from agent.loop import Agent
from agent.skills import SkillLoader, estimate_tokens

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


def test_loader() -> None:
    print("\n[1] 目录扫描与解析")
    loader = SkillLoader()
    check("找到 3 个技能", len(loader) == 3, loader.names())
    check("没有解析错误", loader.errors == [], loader.errors)

    run_tests = loader.get("run-tests")
    check("frontmatter 的 name 生效", run_tests is not None)
    check("description 带触发语", "Use this when" in (run_tests.description if run_tests else ""), run_tests and run_tests.description[:50])
    check("正文被完整保留", "怎么算做完了" in (run_tests.body if run_tests else ""))

    review = loader.get("review-diff")
    check("review-diff 带脚本（形态二）", review is not None and review.has_scripts)
    brief = loader.get("project-brief")
    check("project-brief 走 MCP（形态三）", brief is not None and brief.uses_mcp)
    check("run-tests 是纯提示词（形态一）", run_tests is not None and not run_tests.has_scripts and not run_tests.uses_mcp)


def test_progressive_disclosure() -> None:
    print("\n[2] 渐进披露")
    loader = SkillLoader()
    catalog = loader.catalog_text()

    # 正文里挑几个只在正文出现的词，验证它们没进目录
    body_markers = ["怎么算做完了", "traceback", "速览的时间戳"]
    leaked = [m for m in body_markers if m in catalog]
    check("正文没有漏进目录", leaked == [], leaked)

    ratio = loader.full_tokens() / max(1, loader.catalog_tokens())
    check("披露比 ≥ 5（及格线）", ratio >= 5, round(ratio, 2))
    check("目录足够小（≤ 500 token）", loader.catalog_tokens() <= 500, loader.catalog_tokens())

    check("estimate_tokens 至少为 1", estimate_tokens("") >= 1)


def test_agent_integration() -> None:
    print("\n[3] 与 agent 循环集成")
    agent = Agent(mock=True, persist=False)
    check("use_skill 注册成了工具", "use_skill" in agent.registry.names)
    check("目录进了 system prompt", "run-tests" in agent.system_prompt)
    check("system prompt 里没有正文", "怎么算做完了" not in agent.system_prompt)

    result = agent.registry.call("use_skill", {"name": "review-diff"})
    check("use_skill 能加载正文", result.ok and "五条清单" in result.content)
    bad = agent.registry.call("use_skill", {"name": "不存在"})
    check("不存在的技能返回可用列表", not bad.ok and "run-tests" in bad.content)

    # 重复 attach 不炸（server 复用同一张注册表）
    agent2 = Agent(mock=True, persist=False, registry=agent.registry)
    check("共享注册表时重复 attach 不报错", agent2.skills is not None)


def test_switch() -> None:
    print("\n[4] 总开关 ENABLE_SKILLS=0")
    old = config.enable_skills
    try:
        config.enable_skills = False
        agent = Agent(mock=True, persist=False)
        check("关闭后没有 use_skill 工具", "use_skill" not in agent.registry.names)
        check("关闭后目录不进 prompt", "可用技能" not in agent.system_prompt)
        check("关闭后 skills 为 None", agent.skills is None)
    finally:
        config.enable_skills = old


def main() -> None:
    test_loader()
    test_progressive_disclosure()
    test_agent_integration()
    test_switch()
    print(f"\n结果：{passed} 通过，{failed} 失败")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
