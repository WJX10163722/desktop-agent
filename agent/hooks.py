"""
agent.hooks —— 定制层的硬约束（增量 B）

一句话：hook 是**代码里的 if**，不是提示词里的"请不要"。

为什么必须是代码（作业 3.3 节的翻车点）：
    把"禁止 rm -rf"写进 AGENTS.md，那是建议——模型可能遵守，也可能换个花样重试；
    写成 before_tool() 里的一行 if，它就**不可能被绕过**。
    建议靠模型自觉，约束靠代码不自觉，这是两者的本质区别。

三条 hook：
    1. PreToolUse  写 .env / *.pem / *.key        → 拒绝："密钥不入库，用环境变量"
    2. PreToolUse  rm -rf / git push --force / git reset --hard
                                                    → 拒绝，并给出替代方案
    3. PostToolUse write / edit 之后自动跑测试     → 失败摘要回注（不是整段 traceback）

拒绝时为什么必须给替代方案：
    只说"不行"，模型会换个姿势再试三次（这就是"连试三次"翻车现场）；
    告诉它"用 git revert"，它一步就走到正路上。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .config import ROOT_DIR
from .tools.base import ToolResult

# 快速测试组：改动后跑这一组（最快、覆盖四工具全部行为）。
# 全套 6 组要几十秒，hook 里跑全套会把每次写文件都拖成半分钟——
# "自动测试"如果太慢，用户就会想关掉它，那这条 hook 就白做了。
QUICK_TEST_CMD = [sys.executable, "tests/test_tools.py"]


def _alt(cmd: str, alternative: str) -> str:
    return f"拒绝执行。原因：{cmd} 会造成不可恢复的破坏。替代方案：{alternative}"


# bash 危险命令 → 拒绝理由（拒绝必须带替代方案，见模块注释）
BASH_RULES: list[tuple[str, str]] = [
    (
        r"rm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+|rm\s+-[a-zA-Z]*r[a-zA-Z]*f|rm\s+-[a-zA-Z]*f[a-zA-Z]*r",
        "拒绝 rm -rf。替代方案：先 `git clean -nd` 预览会删什么；已跟踪的文件用 `git restore <文件>` 恢复。",
    ),
    (
        r"git\s+push\s+[^|;]*(-f|--force)",
        "拒绝强推。替代方案：用 `git revert <commit>` 生成一笔反向提交，保住协作历史。",
    ),
    (
        r"git\s+reset\s+--hard",
        "拒绝 git reset --hard。替代方案：单个文件用 `git restore <文件>`，整笔提交用 `git revert`。",
    ),
]

# 这些文件名的改动 = 泄漏密钥的前奏
SECRET_PATTERNS = (".env", ".pem", ".key")


def _is_secret_path(path: str) -> bool:
    lowered = Path(path).name.lower()
    if lowered in ("", "."):
        return True
    # .env / .env.local / prod.env 都算：变体全是换汤不换药的密钥文件
    if lowered.startswith(".env") or lowered.endswith(".env"):
        return True
    return lowered.endswith((".pem", ".key"))


class HookEngine:
    """挂进 Agent 循环的钩子集合，顺带记录验收要用的每一笔账。"""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.denied: list[dict] = []
        self.autotest: dict = {
            "runs": 0, "red": 0, "green": 0, "red_to_green": 0, "injected_lines": 0,
        }
        self._last_red = False

    # ---------------- PreToolUse ----------------
    def pre_tool(self, name: str, args: dict) -> str | None:
        """返回拒绝理由则拦截；返回 None 放行。"""
        if not self.enabled:
            return None

        if name in ("write", "edit"):
            path = str(args.get("path", ""))
            if _is_secret_path(path):
                return self._deny(name, {"tool": name, "path": path}, "密钥不入库，用环境变量")
            # 命令的 -c/--out 之类的参数也可能写文件，但主要入口是 path，先守住主干
            return None

        if name == "bash":
            command = str(args.get("command", ""))
            for pattern, reason in BASH_RULES:
                if re.search(pattern, command):
                    return self._deny(name, {"tool": name, "command": command}, reason)
            return None

        return None

    def _deny(self, tool: str, detail: dict, reason: str) -> str:
        self.denied.append(detail | {"reason": reason})
        return reason

    # ---------------- PostToolUse ----------------
    def post_tool(self, name: str, result: ToolResult) -> str | None:
        """write/edit 成功后自动跑快速测试。返回要回注的失败摘要，None 表示不用回注。"""
        if not self.enabled or name not in ("write", "edit") or not result.ok:
            return None

        ok, fail_lines = self._run_quick_tests()
        self.autotest["runs"] += 1
        if ok:
            self.autotest["green"] += 1
            if self._last_red:
                self.autotest["red_to_green"] += 1
            self._last_red = False
            return None

        self.autotest["red"] += 1
        self._last_red = True
        # 只回注摘要（最多 3 行），不回注整段 traceback——
        # 回注 80 行报错会把上下文挤爆，模型反而找不到重点
        lines = ["[hook] 自动测试未通过，请先修复："] + [f"- {l}" for l in fail_lines]
        self.autotest["injected_lines"] += len(lines)
        return "\n".join(lines)

    def _run_quick_tests(self) -> tuple[bool, list[str]]:
        try:
            proc = subprocess.run(
                QUICK_TEST_CMD,
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, [f"自动测试无法运行：{type(e).__name__}: {e}"]

        ok = proc.returncode == 0
        output = (proc.stdout or "") + (proc.stderr or "")
        fails = [line.strip() for line in output.splitlines() if "FAIL" in line][:3]
        return ok, fails or ["测试退出码非 0，但没有可读的失败行，请手动跑 python tests/test_tools.py"]

    # ---------------- 报告 ----------------
    def summary(self) -> dict:
        return {
            "enabled": self.enabled,
            "denied": list(self.denied),
            "autotest": dict(self.autotest),
        }
