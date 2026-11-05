"""
python -m app.inspect —— 增量作业的验收数字生成器

为什么数字必须由这个命令吐出来（判分第二原则）：
    手写表格会抄错、会美化、会和代码对不上。
    让程序自己量自己，报告里的每个数字都能追溯到这里。

用法：
    python -m app.inspect skills --out skill_report.json
    python -m app.inspect custom --out custom_report.json
    python -m app.inspect skills --mock                      # 离线模式：跳过模型实测
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import agents_md
from agent.config import ROOT_DIR, config
from agent.hooks import HookEngine
from agent.skills import SkillLoader


# ---------------------------------------------------------------- 技能路由实测
# 每条：(用户请求, 期望命中的技能)。expected=None 的是"不该触发"的反例。
TRIALS: list[tuple[str, str | None]] = [
    ("帮我跑一下测试", "run-tests"),
    ("测试为什么挂了？", "run-tests"),
    ("把这段 diff 审一遍", "review-diff"),
    ("帮我 review 一下这次的改动", "review-diff"),
    ("这个项目是干什么的？", "project-brief"),
    ("盘点一下项目结构", "project-brief"),
    ("今天天气怎么样", None),
    ("给我讲个笑话", None),
]

_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)


def _route_once(llm, catalog: str, query: str) -> str | None:
    """让模型只做一件事：看目录选技能名。输出强制成一行 JSON，好解析。"""
    reply = llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "你是技能路由器。根据技能目录判断用户请求该用哪个技能。\n"
                    '只输出一行 JSON：{"skill": "技能名"}，都不适用则输出 {"skill": null}。\n'
                    "不要输出其他内容。\n\n技能目录：\n" + catalog
                ),
            },
            {"role": "user", "content": query},
        ]
    )
    match = _JSON_RE.search(reply.content or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(0)).get("skill")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, str) else None


def build_skills_report(mock: bool = False) -> dict:
    loader = SkillLoader()
    # 开关关掉时，目录不会进上下文——这里的数字要反映"实际进上下文的量"，
    # 而不是"目录文件有多大"。验收现场关开关跑一遍，这一项必须变成 0。
    catalog_tokens = loader.catalog_tokens() if config.enable_skills else 0
    full_tokens = loader.full_tokens()

    report: dict = {
        "enabled": config.enable_skills,
        "skills": loader.summary(),
        "catalog_tokens": catalog_tokens,
        "full_tokens": full_tokens,
        "disclosure_ratio": round(full_tokens / max(1, catalog_tokens), 1) if catalog_tokens else None,
        "loader_errors": loader.errors,
    }

    if not config.enable_skills:
        report["trials"] = []
        report["trials_note"] = "ENABLE_SKILLS=0，技能系统关闭，模型拿不到目录因此不会触发"
        report["false_positive"] = 0
        return report

    if mock:
        report["trials"] = []
        report["trials_note"] = "mock 模式未跑模型实测，验收前请用真实模型跑一次"
        report["false_positive"] = None
        return report

    if not config.api_key:
        report["trials"] = []
        report["trials_note"] = "未配置 DEEPSEEK_API_KEY，无法实测路由"
        report["false_positive"] = None
        return report

    from agent.llm import DeepSeekLLM

    llm = DeepSeekLLM()
    trials = []
    error = None
    for query, expected in TRIALS:
        try:
            hit = _route_once(llm, loader.catalog_text(), query)
            if hit not in (None, "") and hit not in loader.names():
                hit = None  # 模型编了个不存在的技能名，按未命中算
        except Exception as e:  # noqa: BLE001 —— 网络挂了也要出报告
            error = f"{type(e).__name__}: {e}"
            hit = None
        trials.append(
            {"query": query, "expected": expected, "hit": hit, "ok": hit == expected}
        )

    negatives = [t for t in trials if t["expected"] is None]
    report["trials"] = trials
    report["false_positive"] = sum(1 for t in negatives if t["hit"] is not None)
    if error:
        report["trials_error"] = error
    return report


# ---------------------------------------------------------------- 定制层实测
# autotest 的"红转绿"场景：往 tests/test_tools.py 塞一行坏 import，
# hook 跑测试 → 红；再 edit 修掉 → 绿。两次都是真的 hook、真的测试。
RED_MARKER = "import nonexistent_module_xyz__hook_demo"
RED_OLD = "from __future__ import annotations"
RED_BAD = RED_OLD + "\n" + RED_MARKER
RED_FILE = "tests/test_tools.py"


def _run_autotest_scenario() -> dict:
    """红转绿实测。结束时保证文件恢复原状（finally 兜底）。"""
    from agent.loop import Agent

    target = ROOT_DIR / RED_FILE
    backup = target.read_text(encoding="utf-8")
    try:
        agent = Agent(
            mock=True,
            mock_script=[
                {"tool": "edit", "args": {"path": RED_FILE, "old_string": RED_OLD, "new_string": RED_BAD}},
                {"tool": "edit", "args": {"path": RED_FILE, "old_string": RED_BAD, "new_string": RED_OLD}},
                "已按测试失败摘要修复。",
            ],
            persist=False,
        )
        result = agent.run("改一下测试文件（脚本场景）")
        stats = agent.hooks.autotest | {"error": result.error or None}
    finally:
        # 场景中断也不能把仓库留在坏状态
        current = target.read_text(encoding="utf-8")
        if current != backup:
            target.write_text(backup, encoding="utf-8")
    return stats


def build_custom_report(skip_reviewer: bool = False) -> dict:
    loaded = agents_md.load(config.workspace)
    report: dict = {
        "switches": {
            "ENABLE_SKILLS": config.enable_skills,
            "ENABLE_HOOKS": config.enable_hooks,
        },
        "agents_md": {
            "found": loaded["found"],
            "bytes": loaded["bytes"],
            "layers": loaded["layers"],
        },
    }

    # denied：用真实 hook 代码跑两次"会被拒的尝试"，拒绝记录来自 HookEngine 本身
    engine = HookEngine(enabled=config.enable_hooks)
    r1 = engine.pre_tool("write", {"path": ".env"})
    r2 = engine.pre_tool("bash", {"command": "rm -rf build/"})
    report["denied"] = engine.denied
    report["denied_results"] = {"write_env": bool(r1), "bash_rmrf": bool(r2)}
    report["denied_note"] = "以上为真实 PreToolUse 检查的记录，不是手写清单"

    if not config.enable_hooks:
        report["autotest"] = {"runs": 0, "red": 0, "green": 0, "red_to_green": 0, "injected_lines": 0}
        report["autotest_note"] = "ENABLE_HOOKS=0，hook 关闭，autotest 未运行"
        report["reviewer"] = {"runs": 0, "issues_found": 0, "main_context_delta": 0, "subagent_tokens": 0}
        return report

    report["autotest"] = _run_autotest_scenario()

    if skip_reviewer or not config.api_key:
        report["reviewer"] = {"runs": 0, "issues_found": 0, "main_context_delta": 0,
                              "subagent_tokens": 0, "note": "跳过（--skip-reviewer 或无 Key）"}
        return report

    from agent.reviewer import run_reviewer

    try:
        review = run_reviewer()
        report["reviewer"] = {
            "runs": 1,
            "issues_found": review["issues_found"],
            "main_context_delta": review["main_context_delta"],
            "subagent_tokens": review["subagent_tokens"],
        }
        report["reviewer_final"] = review["final"][:800]
    except Exception as e:  # noqa: BLE001 —— 网络/Key 问题不能挡住整份报告
        report["reviewer"] = {"runs": 0, "issues_found": 0, "main_context_delta": 0,
                              "subagent_tokens": 0, "note": f"审查失败：{type(e).__name__}: {e}"}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(prog="app.inspect")
    sub = parser.add_subparsers(dest="target", required=True)

    p_skills = sub.add_parser("skills", help="生成 skill_report.json")
    p_skills.add_argument("--out", default="")
    p_skills.add_argument("--mock", action="store_true", help="跳过模型实测（离线用）")

    p_custom = sub.add_parser("custom", help="生成 custom_report.json")
    p_custom.add_argument("--out", default="")
    p_custom.add_argument("--skip-reviewer", action="store_true", help="跳过真实模型审查")

    sub.add_parser("daemon", help="生成 daemon_report.json（增量 C）")

    args = parser.parse_args()

    if args.target == "skills":
        report = build_skills_report(mock=args.mock)
    elif args.target == "custom":
        report = build_custom_report(skip_reviewer=args.skip_reviewer)
    else:
        print("增量 C 还没实现", file=sys.stderr)
        return 2

    text = json.dumps(report, ensure_ascii=False, indent=1)
    print(text)
    out = getattr(args, "out", "")
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(f"已写入 {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
