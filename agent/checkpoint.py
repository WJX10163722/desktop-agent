"""
agent.checkpoint —— 崩溃续跑（增量 C）

和 session 的区别（答辩会被问）：
    session 存的是"聊过什么"——任务结束后仍然有意义；
    checkpoint 存的是"任务做到第几步"——任务成功结束就该删掉。
    一个是档案，一个是施工现场。

为什么必须每轮落盘而不是退出时保存：
    kill -9 不给任何回调机会。只有"每一步都写完"，重启才能从断点继续，
    否则保存代码本身也会被一起杀死。

原子写沿用 session 的做法：临时文件 + 替换，断电也不毁旧档。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import ROOT_DIR

CHECKPOINT_NAME = "checkpoint.json"
DEFAULT_DIR = ROOT_DIR / "sessions"


def path_for(session_path: Path | None) -> Path:
    """checkpoint 跟会话文件放同一个目录，方便一起备份、一起忽略。"""
    base = session_path.parent if session_path else DEFAULT_DIR
    return Path(base) / CHECKPOINT_NAME


def save(path: Path, user_input: str, step: int, messages: list[dict[str, Any]]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": 1, "user_input": user_input, "step": step, "messages": messages},
            ensure_ascii=False,
        )
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        return None
    return data


def clear(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
