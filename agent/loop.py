"""
agent.loop —— agent 的循环本体

一句话理解智能体：
    智能体 = 「循环」而不是「函数」

普通函数：input -> 处理 -> output，一次结束。
智能体：  while 没干完: 想一步 -> 调工具 -> 看结果 -> 再想一步

这个 loop.py 是整个项目的骨架，后面四天的工作都是往里"装东西"：
    第二步：加工具（ToolRegistry）
    第三步：加终止条件与出错兜底
    第五天：接 WebSocket，把每一步推给界面

现在是**最简单的版本**：一次对话一轮回答，先把"循环"的形状立起来。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import config
from .llm import LLMReply, Message, build_llm
from .prompts import SYSTEM_CHAT


@dataclass
class Turn:
    """一轮对话的记录。后面做 trace 面板、会话持久化全靠它。"""
    index: int
    reply: LLMReply
    elapsed: float = 0.0


@dataclass
class RunResult:
    """一次运行的完整结果，交给 UI / CLI 渲染。"""
    answer: str
    turns: list[Turn] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Agent:
    """最小可用的 agent：管消息历史，管循环，管兜底。"""

    def __init__(self, mock: bool = False, max_turns: int | None = None, system_prompt: str = SYSTEM_CHAT) -> None:
        self.llm = build_llm(mock=mock)
        self.max_turns = max_turns or config.max_turns
        self.system_prompt = system_prompt
        # 消息历史：智能体的"记忆"就只是这一个 list
        self.messages: list[Message] = [{"role": "system", "content": system_prompt}]

    def reset(self) -> None:
        """清空历史，开新会话。界面上的"新建会话"按钮就调它。"""
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def run(self, user_input: str) -> RunResult:
        """跑一轮：把用户的话丢进去，拿回模型的回答。

        注意这里所有的异常都被吃掉并转成 RunResult.error：
        **出错不许崩，要变成一句人话给用户看**（现场演示第二考察点）。
        """
        self.messages.append({"role": "user", "content": user_input})

        turns: list[Turn] = []
        try:
            started = time.time()
            reply = self.llm.chat(self.messages)
            turns.append(Turn(index=0, reply=reply, elapsed=time.time() - started))

            # 只有真的说了话，才把 assistant 消息写进历史，避免污染上下文
            if reply.content:
                self.messages.append({"role": "assistant", "content": reply.content})

            return RunResult(answer=reply.content, turns=turns)

        except Exception as e:  # noqa: BLE001 - 顶层兜底，任何错误都要变成可读文本
            return RunResult(answer="", turns=turns, error=f"{type(e).__name__}: {e}")
