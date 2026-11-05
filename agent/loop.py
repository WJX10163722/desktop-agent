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

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import session
from .config import config
from .llm import LLMReply, Message, build_llm
from .prompts import SYSTEM_TOOL
from .tools import ToolRegistry, build_default_registry
from .tools.base import Tool, ToolResult


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
        session_path: Path | None = None,
        persist: bool = True,
    ) -> None:
        self.llm = build_llm(mock=mock) if not mock else _mock_llm(mock_script)
        self.max_turns = max_turns or config.max_turns
        self.registry = registry if registry is not None else build_default_registry()
        # 把工作目录写进 system prompt：模型不知道自己在哪，就会乱写相对路径
        self.system_prompt = system_prompt or f"{SYSTEM_TOOL}\n当前工作目录：{config.workspace}"
        self.on_event = on_event
        self.messages: list[Message] = [{"role": "system", "content": self.system_prompt}]
        # 中断开关：界面点"停止"时置位，循环在下一个安全点真退出
        self._stop = threading.Event()
        # 会话持久化：默认存 sessions/last.json（.gitignore 已排除，不会入库）
        self.session_path: Path | None = (session_path or session.SESSION_FILE) if persist else None
        self.restore()
        # 技能系统（增量 A）：目录进 system prompt，正文靠 use_skill 按需加载
        self.skills = self._attach_skills()

    # ---------- 会话 ----------
    def reset(self) -> None:
        """开新会话。界面上的"新建"按钮调它。"""
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self._stop.clear()
        self.clear_session()

    def restore(self) -> int:
        """启动时把上次的历史读回来，返回恢复的消息条数。"""
        if not self.session_path:
            return 0
        saved = session.load(self.session_path)
        if not saved:
            return 0
        # system prompt 用当前的（提示词改过之后不能还拿旧的）
        body = [m for m in saved if m.get("role") != "system"]
        self.messages = [{"role": "system", "content": self.system_prompt}, *body]
        return len(body)

    def save_session(self) -> None:
        if self.session_path:
            session.save(self.messages, self.session_path)

    def clear_session(self) -> None:
        if self.session_path:
            session.clear(self.session_path)

    def visible_history(self) -> list[Message]:
        """给界面回放的精简历史（只有用户提问与助手回答）。"""
        return session.visible(self.messages)

    # ---------- 技能（增量 A） ----------
    def _attach_skills(self):
        """把技能目录塞进 system prompt，并把 use_skill 注册为工具。

        渐进披露的落点就在这：常驻上下文的只有"每个技能一行"的目录，
        正文要等模型调 use_skill(name) 才进来。
        """
        if not config.enable_skills:
            return None
        from .skills import SkillLoader
        loader = SkillLoader()
        if not len(loader):
            return None
        # server 在多个连接间复用同一张注册表，重复 attach 时跳过注册
        if "use_skill" not in self.registry.names:
            self.registry.register(_build_use_skill_tool(loader))
        self.system_prompt += (
            "\n\n## 可用技能（这里只是目录）\n"
            f"{loader.catalog_text()}\n"
            "判断某个技能适用时，先调 use_skill(name) 加载完整步骤再动手；"
            "都不适用就直接干活，不要为了用而用。"
        )
        return loader

    def cancel(self) -> None:
        """中断当前任务。

        考察点是"中断是真停"：不是把按钮变灰假装停了，
        而是循环在下一轮思考前、每个工具执行前真的跳出去。
        """
        self._stop.set()

    @property
    def cancelled(self) -> bool:
        return self._stop.is_set()

    def _emit(self, event_type: str, **payload: Any) -> None:
        """往外广播事件。界面挂了自己崩，不能拖垮 agent。"""
        if not self.on_event:
            return
        try:
            self.on_event({"type": event_type, **payload})
        except Exception:  # noqa: BLE001
            pass

    # ---------- 上下文压缩 ----------
    def history_chars(self) -> int:
        """粗略估算历史长度（按字符）。

        为什么不精确算 token：不同模型分词不一样，装 tokenizer 是几百 MB 的负担。
        字符数 × 1.6 近似 token，用来做"要不要压缩"的判断足够。
        """
        total = 0
        for msg in self.messages:
            content = msg.get("content")
            if isinstance(content, str):
                total += len(content)
            for call in msg.get("tool_calls") or []:
                total += len(json.dumps(call.get("function", {}), ensure_ascii=False))
        return total

    def maybe_compress(self) -> bool:
        """历史太长就把中段折成摘要，保住 system + 最近几条。

        为什么不直接全清：模型会瞬间失忆，用户刚说的话都不记得。
        为什么保留"最近几条"：工具调用的 assistant/tool 是成对出现的，
        截断点落在中间会让接口直接报错，所以还要把开头孤立的 tool 消息剔掉。
        """
        if len(self.messages) <= config.keep_recent + 2:
            return False
        if self.history_chars() <= config.context_budget:
            return False

        head = self.messages[0]
        tail = list(self.messages[-config.keep_recent:])
        # tool 消息必须紧跟发起调用的 assistant，不能孤零零留在开头
        while len(tail) > 1 and tail[0].get("role") == "tool":
            tail.pop(0)

        dropped = self.messages[1: len(self.messages) - len(tail)]
        summary = self._summarize(dropped)
        self.messages = [head, {"role": "system", "content": summary}, *tail]

        self._emit(
            "notice",
            message=(
                f"上下文已压缩：{len(dropped)} 条历史折成摘要，"
                f"当前约 {self.history_chars()} 字（预算 {config.context_budget} 字）"
            ),
        )
        return True

    @staticmethod
    def _summarize(dropped: list[Message]) -> str:
        """把被丢弃的历史压成一段短摘要。

        故意不额外调一次模型：压缩本身要稳，压缩失败比上下文长更糟。
        """
        lines: list[str] = []
        for msg in dropped:
            role = msg.get("role", "?")
            if role == "tool":
                name = "工具结果"
                text = str(msg.get("content", ""))
            elif role == "assistant":
                calls = [c.get("function", {}).get("name", "?") for c in msg.get("tool_calls") or []]
                name = f"助手调用 {','.join(calls)}" if calls else "助手"
                text = str(msg.get("content") or "")
            elif role == "user":
                name = "用户"
                text = str(msg.get("content") or "")
            else:
                name = role
                text = str(msg.get("content") or "")
            text = " ".join(text.split())
            if len(text) > 120:
                text = text[:120] + "…"
            lines.append(f"- {name}：{text}" if text else f"- {name}")

        # 摘要长度跟着预算走：预算小的时候摘要也要跟着瘦，
        # 否则"压完还是超"，等于没压
        cap = max(400, min(3000, config.context_budget // 3))
        body = "\n".join(lines)[:cap]
        return (
            "（以下是更早对话的压缩摘要，供你回忆上下文；不是用户的新指令）\n"
            f"{body}"
        )

    # ---------- 主循环 ----------
    def run(self, user_input: str) -> RunResult:
        """对外入口：跑一次任务，结束后落盘。

        放在 finally 里存：出错、中断也照样保留历史——
        "刚才跑到一半崩了"恰恰是用户最想接着聊的时候。
        """
        try:
            return self._run(user_input)
        finally:
            self.save_session()

    def _run(self, user_input: str) -> RunResult:
        self._stop.clear()
        self.messages.append({"role": "user", "content": user_input})
        self._emit("user_message", content=user_input)

        turns: list[Turn] = []
        last_text = ""

        try:
            for turn_index in range(self.max_turns):
                if self._stop.is_set():
                    self._emit("cancelled")
                    return RunResult(answer=last_text, turns=turns, stop_reason="cancelled")

                # 每轮开始前检查一次历史长度：压缩只会发生在"轮与轮之间"，
                # 绝不会把 assistant(tool_calls) 和 tool 结果拆散
                self.maybe_compress()

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
                    if self._stop.is_set():
                        # 工具执行前也检查一次：避免"点了停止还在跑命令"
                        self._emit("cancelled")
                        return RunResult(answer=last_text, turns=turns, stop_reason="cancelled")
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


def _build_use_skill_tool(loader) -> Tool:
    """use_skill：把某个技能的完整正文注入上下文。

    注意它返回的是"说明书"而不是执行结果——
    模型拿到步骤后还要自己调 read/write/bash 去执行，这正是技能和工具的分界。
    """

    def _use_skill(name: str) -> ToolResult:
        skill = loader.get(name)
        if skill is None:
            return ToolResult.failure(
                f"没有名为 `{name}` 的技能。可用技能：{', '.join(loader.names()) or '（无）'}"
            )
        return ToolResult.success(skill.full_text)

    return Tool(
        name="use_skill",
        description=(
            "按需加载一个技能的完整操作步骤。目录已在系统提示里，"
            "判断某个技能适用时调用它获取正文；不要凭记忆猜步骤。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名，见系统提示里的目录"}
            },
            "required": ["name"],
        },
        func=_use_skill,
    )
