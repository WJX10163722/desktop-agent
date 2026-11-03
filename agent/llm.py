"""
agent.llm —— 模型适配层

这一层存在的意义：**把"怎么调模型"和"循环怎么写"彻底分开**。
明天你想换 GPT / 通义 / 本地 Ollama，只改这个文件，loop.py 一个字不用动。

对外只暴露一个接口：
    llm.chat(messages) -> LLMReply

messages 统一是 OpenAI 格式：
    [{"role": "system" | "user" | "assistant" | "tool", "content": "..."}]
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import requests

from .config import config

Message = dict[str, Any]


@dataclass
class LLMReply:
    """模型返回的一次结果。

    为什么要单独封装一层：
    - 各家 SDK 返回格式千奇百怪（dict / pydantic / 对象）
    - loop 只关心三件事：说的话、要调的工具、用量
    """
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class DeepSeekLLM:
    """OpenAI 兼容接口的调用实现（DeepSeek / OpenAI / 大多数国产模型都通用）。"""

    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None) -> None:
        self.model = model or config.model
        self.base_url = base_url or config.base_url
        self.api_key = api_key or config.require_key()

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMReply:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,  # 编码任务要稳定，别让它自由发挥
        }
        # 只有真的要用到工具时才传 tools 字段（省 token，也避免模型乱调）
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=config.request_timeout)
        except requests.exceptions.RequestException as e:
            # 出错不装人：把原始异常包装成人话，交给上层展示
            raise RuntimeError(f"模型请求失败（网络/超时/端点错误）：{e}") from e

        if resp.status_code != 200:
            raise RuntimeError(f"模型返回 {resp.status_code}：{resp.text[:500]}")

        data = resp.json()
        choice = data["choices"][0]["message"]
        usage = data.get("usage", {})

        return LLMReply(
            content=choice.get("content") or "",
            tool_calls=choice.get("tool_calls") or [],
            usage=usage,
        )


class MockLLM:
    """假模型：不发请求、不花钱，用来验证"循环"本身对不对。

    这是我认为整个项目里最值钱的一个小工具：
    没网、没 Key、CI 里跑测试的时候，你照样能把 loop 跑通。
    后面测试工具调用、异常处理，全靠它喂剧本。

    剧本格式（一轮一条）：
        字符串                         → 直接回一句话
        {"tool": "read", "args": {...}} → 发起一次工具调用
    """

    def __init__(self, script: list[str | dict] | None = None) -> None:
        self.script = script or ["[mock] 我是假模型，循环跑通了。"]
        self.calls: list[dict] = []  # 记录每次被调用的内容，方便断言

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMReply:
        self.calls.append({"messages": messages, "tools": tools})
        idx = min(len(self.calls) - 1, len(self.script) - 1)
        step = self.script[idx]

        if isinstance(step, dict) and "tool" in step:
            call = {
                "id": f"call_{idx}",
                "type": "function",
                "function": {
                    "name": step["tool"],
                    "arguments": json.dumps(step.get("args", {}), ensure_ascii=False),
                },
            }
            return LLMReply(content=step.get("content", ""), tool_calls=[call])

        return LLMReply(content=str(step))


def build_llm(mock: bool = False):
    """工厂函数：上层只认这个入口，不用关心具体是哪个实现。"""
    if mock:
        return MockLLM()
    return DeepSeekLLM()


def dump_messages(messages: list[Message]) -> str:
    """调试用：把消息历史打成可读文本。"""
    return json.dumps(messages, ensure_ascii=False, indent=2)
