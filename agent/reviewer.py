"""
agent.reviewer —— 审查子 agent（增量 B）

它存在的唯一理由：**上下文隔离**。

    在主对话里让模型自己 review 自己的 diff，几十 KB 的 diff 会挤进主上下文，
    后面每一轮都背着它跑。子 agent 在自己的上下文里读完整个 diff，
    只把最后一份"问题清单"传回来——主上下文的增量就是清单本身那几百字。

纪律（作业 3.1③）：
    1. 只读：read 工具 + 只限 git diff/status/log 的 bash
    2. 固定五条检查清单，不自由发挥
    3. 只回最后一条消息，中间过程不出子 agent 的上下文
"""
from __future__ import annotations

from .config import config
from .tools import ToolRegistry
from .tools.base import Tool, ToolResult
from .tools.fs import tool_read

# reviewer 能跑的命令白名单：看 diff 够用，干别的没门
REVIEW_BASH_ALLOW = ("git diff", "git status", "git log", "git show", "git ls-files")

CHECKLIST = """\
1. 有没有改到不该改的文件（.env、锁文件、生成产物、无关文件）
2. 有没有新增依赖（requirements.txt 变了却没有说明理由）
3. 有没有 `except: pass` 或裸 `except Exception` 吞掉错误
4. 改了逻辑但没加/没改对应测试
5. diff 里有没有调试残留（`print(`、`TODO`、被注释掉的代码块）"""

REVIEWER_PROMPT = f"""你是代码审查员，只读不写。当前工作目录里的未提交改动就是审查对象。

工作方式（严格按这三步走，不要发散）：
1. 跑一次 `git diff --stat` 看改动范围
2. 跑一次 `git diff` 读内容；diff 太长就按文件分批：`git diff -- <路径>`
3. 直接给出审查结论，不要反复查看同一份内容

注意：这是 Windows 的命令行，不支持管道 `|`、`head`、`grep`、`tail`。
只跑 git 命令本身，需要筛选就自己读输出。

检查清单（逐条过，只报有问题的条目）：
{CHECKLIST}

输出格式（严格执行，这是给主对话省上下文的）：
- 每个问题一行，以 `- ` 开头：`[文件:位置] 清单条目：不超过 40 字的说明`
- 最多 5 行，总长不超过 300 字；超了说明你在写作文，不是在审查
- 全部干净时只输出一行：`五项检查全部干净`
- 不要输出中间过程、不要贴 diff、不要道歉和铺垫。"""


def build_reviewer_registry() -> ToolRegistry:
    """只读注册表：read 全功能；bash 砍到只剩 git 只读命令。"""
    registry = ToolRegistry()

    registry.register(tool_read)

    def _review_bash(command: str) -> ToolResult:
        lowered = command.strip().lower()
        if not any(lowered.startswith(prefix) for prefix in REVIEW_BASH_ALLOW):
            allowed = ", ".join(REVIEW_BASH_ALLOW)
            return ToolResult.failure(f"审查员只允许只读命令（{allowed}），其他一律不跑。")
        # 借用完整版 bash 工具的执行函数（tool_bash 是 Tool 对象，func 才是函数）
        from .tools.shell import run_bash

        return run_bash(command)

    registry.register(
        Tool(
            name="bash",
            description="只读查看命令（仅限 git diff / status / log / show / ls-files）",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string", "description": "git 只读命令"}},
                "required": ["command"],
            },
            func=_review_bash,
        )
    )
    return registry


def run_reviewer(scope: str = "") -> dict:
    """跑一次审查，返回最终报告和"上下文账目"。

    main_context_delta 是这个子 agent 价值的直接体现：
    主对话只增加最终报告那一小段，diff 的大身体留在子 agent 里。
    """
    from .loop import Agent

    reviewer = Agent(
        system_prompt=REVIEWER_PROMPT,
        registry=build_reviewer_registry(),
        persist=False,  # 审查是临时会话，不该污染主对话的持久化历史
        max_turns=12,  # 读 diff 本身要几轮，给太少会在中途被掐断（踩过：只回了"我先看看"）
    )
    prompt = "请审查当前的未提交改动。" + (f"重点关注：{scope}" if scope else "")
    result = reviewer.run(prompt)

    answer = (result.answer or "").strip()
    # 强制收尾：模型有时读完 diff 就以一句"我先看看"结束（正文为空）。
    # （下面 if 里补一次不带工具的请求）
    # 这里再问一次，且不给工具——它只能写结论。这是子 agent 里唯一一次额外请求，
    # 换来的是"一定有报告"，而不是"有时有"。
    if not answer or result.stop_reason != "done":
        try:
            reply = reviewer.llm.chat(
                reviewer.messages
                + [
                    {
                        "role": "user",
                        "content": "不要再调用工具，直接输出审查结论：每个问题一行 `[位置] 清单条目：说明`；没有问题就输出『五项检查全部干净』。",
                    }
                ]
            )
            answer = (reply.content or "").strip() or answer
        except Exception:  # noqa: BLE001 - 收尾失败就用手上已有的
            pass
    # 计数：以 "- "/"[ "/"* " 开头的行算一个问题（说"全部干净"就是 0）
    if "五项检查全部干净" in answer:
        issues: list[str] = []
    else:
        issues = [
            line.strip()
            for line in answer.splitlines()
            if line.strip().startswith(("-", "[", "*"))
        ]

    # 主上下文只留 500 字以内的清单：这是子 agent 存在的意义，
    # 不是说"隔离"了却把整份报告塞回去。
    injected = answer if len(answer) <= 500 else answer[:480].rstrip() + "…（清单过长已截断）"

    return {
        "final": injected,
        "issues_found": len(issues),
        "main_context_delta": len(injected),
        "subagent_tokens": max(1, round(reviewer.history_chars() / 1.6)),
        "turns": result.turns,
        "error": result.error or None,
    }
