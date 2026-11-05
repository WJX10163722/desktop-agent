"""
python -m app.selftest —— v1.0 基线自检（增量作业第 1 节要求）

跑五组既有测试并汇总成 JSON。增量作业的规矩：
    动手加技能之前，四工具、界面链路、MCP 必须全绿——
    增量是加法，不是补锅。

用法：
    python -m app.selftest
    python -m app.selftest --out selftest.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES = [
    "test_tools", "test_server", "test_mcp", "test_agent",
    "test_session", "test_skills", "test_custom", "test_daemon",
]
_RESULT_RE = re.compile(r"(\d+)\s*\S*通过\S*\s*[，,]\s*(\d+)\s*\S*失\S*")


def run_suite(name: str) -> dict:
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PATH": __import__("os").environ.get("PATH", "")}
    proc = subprocess.run(
        [sys.executable, f"tests/{name}.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",  # 子进程被我们强制成 utf-8 输出，这里也要按 utf-8 读
        errors="replace",
        env={**__import__("os").environ, **env},
        timeout=300,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    match = None
    for line in output.splitlines():
        m = _RESULT_RE.search(line)
        if m:
            match = m  # 取最后一行结果
    if match:
        passed, failed = int(match.group(1)), int(match.group(2))
    else:
        passed, failed = 0, 1
    return {
        "suite": name,
        "passed": passed,
        "failed": failed,
        "ok": proc.returncode == 0 and failed == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="", help="把结果写到 JSON 文件")
    args = parser.parse_args()

    suites = [run_suite(s) for s in SUITES]
    total_passed = sum(s["passed"] for s in suites)
    total_failed = sum(s["failed"] for s in suites)
    report = {
        "suites": suites,
        "total_passed": total_passed,
        "total_failed": total_failed,
        "ok": all(s["ok"] for s in suites),
    }

    text = json.dumps(report, ensure_ascii=False, indent=1)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
