#!/usr/bin/env python3
"""collect_diff.py —— 给 review-diff 技能用的改动摘要。

为什么不让模型自己敲 git diff：
    git diff 全量输出可能几百行，模型会把注意力耗在无关文件上。
    这个脚本先给统计，再给裁剪过的 diff，让"审"这个动作有的放矢。

用法（由技能通过 bash 工具调用）：
    python skills/review-diff/scripts/collect_diff.py [最多行数，默认 120]
"""
from __future__ import annotations

import subprocess
import sys

# 这些文件的改动通常是噪音，审了也没意义
IGNORE_SUFFIXES = (".lock", ".png", ".jpg", ".ico", ".pyc")
IGNORE_NAMES = ("uv.lock", "poetry.lock", "package-lock.json")


def run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, errors="replace")
    return result.stdout


def _noise(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(IGNORE_SUFFIXES) or any(n in lowered for n in IGNORE_NAMES)


def main() -> int:
    limit = 120
    if len(sys.argv) > 1:
        try:
            limit = max(20, min(400, int(sys.argv[1])))
        except ValueError:
            print("参数应是行数（整数），使用默认 120", file=sys.stderr)

    if not run(["git", "rev-parse", "--is-inside-work-tree"]).strip():
        print("ERROR: 当前目录不是 git 仓库")
        return 1

    stat = run(["git", "diff", "--stat"])
    untracked = [l for l in run(["git", "ls-files", "--others", "--exclude-standard"]).splitlines() if l]

    print("== 改动统计 ==")
    print(stat.strip() or "（没有已跟踪文件的改动）")
    if untracked:
        print(f"\n== 未跟踪的新文件（{len(untracked)} 个）==")
        for name in untracked[:20]:
            print(f"  + {name}")

    diff = run(["git", "diff", "--unified=2"])
    lines = []
    skipped = 0
    for line in diff.splitlines():
        # 只保留文件头和有实质内容的行，跳过纯上下文
        if line.startswith(("diff --git", "@@", "+", "-")) and not line.startswith(("+++", "---")):
            path = line[11:].split("\t")[0] if line.startswith("diff --git") else ""
            if path and _noise(path):
                skipped += 1
                continue
            if not path and any(_noise(l.split("\t")[0]) for l in lines[-1:] if l.startswith("diff --git")):
                continue
            lines.append(line)

    print(f"\n== diff（裁剪到 {limit} 行，略过 {skipped} 行噪音文件）==")
    body = "\n".join(lines)
    if len(lines) > limit:
        body = "\n".join(lines[:limit]) + f"\n…（还有 {len(lines) - limit} 行，需要更多就说\"继续\"）"
    print(body or "（diff 为空）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
