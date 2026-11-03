"""
agent.tools.base —— 工具协议

这一段是整个项目最"有思想"的地方，答辩被问到的概率最高：

    工具不是函数，是「协议」。

模型看不到你的 Python 代码，它只能看到三样东西：
    1. name        工具叫什么（模型靠这个决定调不调）
    2. description 什么时候该用它（写不清 = 模型乱调或干脆不调）
    3. parameters  JSON Schema，参数名 + 类型 + 说明

所以下面 Tool 这个类，本质就是"把 Python 函数包装成模型能读懂的说明书"。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class ToolResult:
    """工具执行结果。

    关键约定：**失败也是一条正常消息**，不是异常。
    异常会炸掉循环；而"失败消息"模型能读、能自己改、能重试。
    """
    ok: bool = True
    content: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, content: str, **meta: Any) -> "ToolResult":
        return cls(ok=True, content=content, meta=meta)

    @classmethod
    def failure(cls, content: str, **meta: Any) -> "ToolResult":
        return cls(ok=False, content=content, meta=meta)

    def to_message_content(self) -> str:
        """转成喂回模型的那段文本。

        加 OK / ERROR 前缀是故意的：
        模型对 "ERROR:" 开头的内容会倾向于换个做法，而不是以为自己成功了。
        """
        return f"{'OK' if self.ok else 'ERROR'}\n{self.content}"


@dataclass
class Tool:
    """一个工具的完整定义。"""
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., ToolResult]

    def schema(self) -> dict[str, Any]:
        """转成 OpenAI / DeepSeek 的 function calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表：装配、查找、调用、兜底，全在这一处。

    兜底是重点（现场演示考察点"出错不装人"）：
    - 工具名不存在 → 告诉模型有哪些工具可用
    - 参数不是合法 JSON → 告诉模型哪里错了
    - 缺必填参数 → 列出来
    - 工具内部抛异常 → 包成 ERROR 消息，不让循环崩
    """

    def __init__(self, workspace: str | Path | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self.workspace = Path(workspace).resolve() if workspace else None

    # ---------- 装配 ----------
    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具名重复：{tool.name}")
        self._tools[tool.name] = tool

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        """喂给模型的工具清单。空注册表返回空列表，调用方就不传 tools 字段。"""
        return [t.schema() for t in self._tools.values()]

    # ---------- 调用 ----------
    def call(self, name: str, arguments: str | dict | None) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(
                f"没有名为 `{name}` 的工具。可用工具：{', '.join(self.names) or '（无）'}"
            )

        try:
            args = self._parse_args(arguments)
        except ValueError as e:
            return ToolResult.failure(f"参数不是合法 JSON，请检查引号和逗号：{e}")

        missing = [k for k in tool.parameters.get("required", []) if k not in args]
        if missing:
            return ToolResult.failure(f"缺少必填参数 {missing}。该工具参数定义：{tool.parameters}")

        args = self._coerce_args(args, tool.parameters)

        try:
            return tool.func(**args)
        except TypeError as e:
            return ToolResult.failure(f"参数不匹配：{e}")
        except Exception as e:  # noqa: BLE001 - 工具内部任何异常都要兜住
            return ToolResult.failure(f"{type(e).__name__}: {e}")

    # ---------- 内部 ----------
    @staticmethod
    def _parse_args(arguments: str | dict | None) -> dict:
        if arguments is None or arguments == "":
            return {}
        if isinstance(arguments, dict):
            return arguments
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as e:
            raise ValueError(str(e)) from e
        if not isinstance(parsed, dict):
            raise ValueError(f"参数应该是 JSON 对象，实际拿到 {type(parsed).__name__}")
        return parsed

    @staticmethod
    def _coerce_args(args: dict, schema: dict) -> dict:
        """模型经常把整数写成 "10"（字符串）。

        这一步做类型纠正，能挡掉大量"看起来莫名其妙的报错"。
        """
        props = schema.get("properties", {})
        out = dict(args)
        for key, spec in props.items():
            if key not in out:
                continue
            value, want = out[key], spec.get("type")
            try:
                if want == "integer" and isinstance(value, str):
                    out[key] = int(value)
                elif want == "number" and isinstance(value, str):
                    out[key] = float(value)
                elif want == "boolean" and isinstance(value, str):
                    out[key] = value.strip().lower() in {"true", "1", "yes"}
                elif want == "string" and not isinstance(value, str):
                    out[key] = str(value)
            except (TypeError, ValueError):
                pass  # 转不了就原样交给工具，让工具给出人话报错
        return out
