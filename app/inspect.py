"""
python -m app.inspect —— 增量作业的验收数字生成器

为什么数字必须由这个命令吐出来（判分第二原则）：
    手写表格会抄错、会美化、会和代码对不上。
    让程序自己量自己，报告里的每个数字都能追溯到这里。

用法：
    python -m app.inspect skills --out skill_report.json
    python -m app.inspect custom --out custom_report.json   （增量 B 做完后）
    python -m app.inspect skills --mock                      # 离线模式：跳过模型实测
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import config
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
    catalog_tokens = loader.catalog_tokens()
    full_tokens = loader.full_tokens()

    report: dict = {
        "skills": loader.summary(),
        "catalog_tokens": catalog_tokens,
        "full_tokens": full_tokens,
        "disclosure_ratio": round(full_tokens / max(1, catalog_tokens), 1),
        "loader_errors": loader.errors,
    }

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


def main() -> int:
    parser = argparse.ArgumentParser(prog="app.inspect")
    sub = parser.add_subparsers(dest="target", required=True)

    p_skills = sub.add_parser("skills", help="生成 skill_report.json")
    p_skills.add_argument("--out", default="")
    p_skills.add_argument("--mock", action="store_true", help="跳过模型实测（离线用）")

    sub.add_parser("custom", help="生成 custom_report.json（增量 B）")
    sub.add_parser("daemon", help="生成 daemon_report.json（增量 C）")

    args = parser.parse_args()

    if args.target == "skills":
        report = build_skills_report(mock=args.mock)
    elif args.target == "custom":
        print("增量 B 还没实现，先做技能系统", file=sys.stderr)
        return 2
    else:
        print("增量 C 还没实现", file=sys.stderr)
        return 2

    text = json.dumps(report, ensure_ascii=False, indent=1)
    print(text)
    if getattr(args, "out", ""):
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
