"""
agent.session —— 会话持久化

为什么值得单独做一块：
    关掉窗口，昨天读过的文件、聊过的结论全没了，用户只能重讲一遍。
    这不是"加分项"，这是"能不能叫产品"的分界线。

设计上的三条自我约束：
    1. 只存 JSON，不引数据库。一个文件够了，出问题还能用记事本打开看。
    2. 原子写。写到一半断电，不能把上一份好会话也毁掉。
    3. 读坏了不报错。文件被手改坏了，就当没有历史，从空会话开始。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import ROOT_DIR

SESSION_DIR = ROOT_DIR / "sessions"
SESSION_FILE = SESSION_DIR / "last.json"


def save(messages: list[dict[str, Any]], path: Path | None = None) -> bool:
    """把历史写成 JSON（原子写：临时文件 + 替换）。"""
    target = Path(path or SESSION_FILE)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "messages": messages}, ensure_ascii=False, indent=1)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)
        return True
    except OSError:
        return False


def load(path: Path | None = None) -> list[dict[str, Any]] | None:
    """读回历史。文件不存在、格式坏了，都返回 None（当成没有历史）。"""
    target = Path(path or SESSION_FILE)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    messages = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(messages, list):
        return None
    return sanitize(messages)


def clear(path: Path | None = None) -> None:
    """新建会话时把旧文件删掉，避免下次启动又把旧历史读回来。"""
    target = Path(path or SESSION_FILE)
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass


def sanitize(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """清洗历史，保证它能被模型接口接受。

    必须处理的两个坑：
    1. 非法 role（手改文件、旧版本格式）→ 丢弃
    2. assistant 带 tool_calls 但后面没有对应的 tool 结果（保存时正好断在中途）
       → 整对丢掉。否则下一次请求接口直接 400，用户会以为"程序坏了"。
    """
    allowed = {"system", "user", "assistant", "tool"}
    cleaned: list[dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") not in allowed:
            continue
        cleaned.append(msg)

    # 从后往前找"悬空的工具调用"：assistant(tool_calls) 之后必须紧跟全部 tool 结果
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(cleaned):
        msg = cleaned[i]
        calls = msg.get("tool_calls") or []
        if msg.get("role") == "assistant" and calls:
            need = {c.get("id") for c in calls}
            j = i + 1
            got = set()
            while j < len(cleaned) and cleaned[j].get("role") == "tool":
                got.add(cleaned[j].get("tool_call_id"))
                j += 1
            if need.issubset(got):
                out.extend(cleaned[i:j])
            # 不完整就整段跳过，不留下"半截调用"
            i = j
            continue
        out.append(msg)
        i += 1

    # 末尾若是没人回应的 user 消息，保留它反而是好事（用户上次问了一半），不动
    return out


def visible(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给界面回放用的精简版：只留用户说的话和助手的最终回答。

    为什么要把工具消息过滤掉：界面回放历史时画不出"当时的 trace 卡片"，
    硬塞进去只会出现一堆没有对应卡片的白字。
    """
    out = []
    for msg in messages:
        role = msg.get("role")
        if role in {"user", "assistant"}:
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                out.append({"role": role, "content": content})
    return out
