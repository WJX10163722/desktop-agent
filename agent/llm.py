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
import time
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
        # 注意：这里不校验 Key，等到真正发请求时才校验。
        # 否则没配 Key 时连窗口都打不开（构建 Agent 就抛异常），体验极差。
        self.api_key = api_key or config.api_key

    def _post_with_retry(self, url: str, headers: dict, payload: dict):
        """带退避重试的 POST。

        为什么必须有：
            现场演示时网络抖一下、服务端偶发 5xx，如果不重试，
            界面上就是一句红字"模型请求失败"，整个 demo 就废了。

        只重试"值得重试"的错误：
            网络异常 / 超时 / 429 限流 / 5xx 服务端错误。
        不重试 4xx（Key 错了、参数错了，重试一万次也没用，只会浪费 3 倍时间）。
        """
        attempts = max(1, config.max_retries)
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=config.request_timeout)
            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt == attempts - 1:
                    break
                time.sleep(min(2 ** attempt, 8))  # 1s → 2s → 4s，别把服务端打得更惨
                continue

            if resp.status_code == 200:
                return resp

            # 只有临时的服务端问题才值得重试
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = RuntimeError(f"模型返回 {resp.status_code}：{resp.text[:200]}")
                if attempt == attempts - 1:
                    break
                time.sleep(min(2 ** attempt, 8))
                continue

            # 4xx：直接失败，别浪费时间
            raise RuntimeError(f"模型返回 {resp.status_code}：{resp.text[:500]}")

        raise RuntimeError(
            f"模型请求失败（已重试 {attempts} 次）：{last_error}"
        )

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMReply:
        if not self.api_key:
            raise RuntimeError(
                "还没有配置 API Key：请把 .env.example 复制成 .env，填入 DEEPSEEK_API_KEY 后重启程序。"
            )
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

        resp = self._post_with_retry(url, headers, payload)

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
