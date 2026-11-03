"""
agent.loop —— agent 的循环本体

一句话理解智能体：
    智能体 = 「循环」而不是「函数」

    普通函数：input -> 处理 -> output，一次结束
    智能体：  while 没干完:
                  让模型想一步
                  如果它要调工具 -> 执行 -> 结果塞回历史
                  如果它不要调工具 -> 说明它觉得干完了 -> 结束

这一版（step2）多了三样东西：
    1. 工具调用：模型说"我要 read main.py"，我们去执行，再把结果喂回去
    2. 轮数上限：模型可能陷入"调工具上瘾"，必须有刹车
    3. 事件回调：每一步都往外广播，第五天的界面就是靠它实时显示
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import config
from .llm import LLMReply, Message, build_llm
from .prompts import SYSTEM_TOOL
from .tools import ToolRegistry, build_default_registry


@dataclass
class ToolCallRecord:
    """一次工具调用的记录，给 trace 面板用（评分里 trace 占 5 分）。"""
    name: str
    arguments: str
    ok: bool
    content: str
    elapsed: float = 0.0


@dataclass
class Turn:
    """一轮"模型思考"。一轮里可能包含多个工具调用。"""
    index: int
    reply: LLMReply
    elapsed: float = 0.0
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


@dataclass
class RunResult:
    """一次运行的完整结果。界面 / CLI 只认这一个对象。"""
    answer: str = ""
    turns: list[Turn] = field(default_factory=list)
    error: str | None = None
    stop_reason: str = "done"  # done | max_turns | error

    @property
    def ok(self) -> bool:
        return self.error is None


EventHook = Callable[[dict[str, Any]], None]


class Agent:
    """能调用工具的智能体。"""

    def __init__(
        self,
        mock: bool = False,
        mock_script: list | None = None,
        max_turns: int | None = None,
        system_prompt: str | None = None,
        registry: ToolRegistry | None = None,
        on_event: EventHook | None = None,
    ) -> None:
        self.llm = build_llm(mock=mock) if not mock else _mock_llm(mock_script)
        self.max_turns = max_turns or config.max_turns
        self.registry = registry if registry is not None else build_default_registry()
        # 把工作目录写进 system prompt：模型不知道自己在哪，就会乱写相对路径
        self.system_prompt = system_prompt or f"{SYSTEM_TOOL}\n当前工作目录：{config.workspace}"
        self.on_event = on_event
        self.messages: list[Message] = [{"role": "system", "content": self.system_prompt}]

    # ---------- 会话 ----------
    def reset(self) -> None:
        """开新会话。界面上的"新建"按钮调它。"""
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def _emit(self, event_type: str, **payload: Any) -> None:
        """往外广播事件。界面挂了自己崩，不能拖垮 agent。"""
        if not self.on_event:
            return
        try:
            self.on_event({"type": event_type, **payload})
        except Exception:  # noqa: BLE001
            pass

    # ---------- 主循环 ----------
    def run(self, user_input: str) -> RunResult:
        self.messages.append({"role": "user", "content": user_input})
        self._emit("user_message", content=user_input)

        turns: list[Turn] = []
        last_text = ""

        try:
            for turn_index in range(self.max_turns):
                started = time.time()
                reply = self.llm.chat(self.messages, tools=self.registry.schemas())
                turn = Turn(index=turn_index, reply=reply, elapsed=time.time() - started)
                turns.append(turn)
                self._emit("assistant_message", content=reply.content, tool_calls=reply.tool_calls)

                # 没有工具调用 = 模型认为任务结束了
                if not reply.has_tool_calls:
                    last_text = reply.content
                    if last_text:
                        self.messages.append({"role": "assistant", "content": last_text})
                    return RunResult(answer=last_text, turns=turns, stop_reason="done")

                # 有工具调用：先把 assistant 这条（带 tool_calls）写进历史，
                # 否则下一轮模型会看到"没有来源的 tool 消息"而报错
                self.messages.append({
                    "role": "assistant",
                    "content": reply.content or None,
                    "tool_calls": reply.tool_calls,
                })
                if reply.content:
                    last_text = reply.content

                for call in reply.tool_calls:
                    record = self._execute_tool_call(call)
                    turn.tool_calls.append(record)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": f"{'OK' if record.ok else 'ERROR'}\n{record.content}",
                    })

            # 刹车踩到底：模型一直不肯收尾
            return RunResult(
                answer=last_text,
                turns=turns,
                error=f"已达到最大轮数 {self.max_turns}，模型仍在调用工具，已强制停止。",
                stop_reason="max_turns",
            )

        except Exception as e:  # noqa: BLE001 - 顶层兜底：出错不装人
            self._emit("error", message=f"{type(e).__name__}: {e}")
            return RunResult(answer=last_text, turns=turns, error=f"{type(e).__name__}: {e}", stop_reason="error")

    def _execute_tool_call(self, call: dict) -> ToolCallRecord:
        fn = call.get("function") or {}
        name = fn.get("name", "")
        raw_args = fn.get("arguments", "") or ""

        self._emit("tool_start", name=name, arguments=raw_args)
        started = time.time()
        result = self.registry.call(name, raw_args)
        elapsed = time.time() - started

        self._emit(
            "tool_end",
            name=name,
            arguments=raw_args,
            ok=result.ok,
            content=result.content,
            elapsed=elapsed,
        )
        return ToolCallRecord(
            name=name, arguments=raw_args, ok=result.ok, content=result.content, elapsed=elapsed
        )


def _mock_llm(script: list | None):
    from .llm import MockLLM
    return MockLLM(script=script)
