"""
agent.agents_md —— AGENTS.md 自动注入（增量 B）

AGENTS.md 是"给 agent 看的项目地图"，不是项目文档：
    文档回答"这段代码是什么"（三个月后全过时）；
    地图回答"怎么跑、哪里有坑"（什么时候都对）。
    作业的纪律：≤ 300 行、不贴代码片段。

两层结构（作业 3.1）：
    全局 ~/.mini_agents/AGENTS.md   —— 个人习惯，跟着人走
    项目级 <向上找>/AGENTS.md       —— 项目约定，跟着仓库走
    拼接顺序：全局在前，项目在后（更具体的约定放在离用户最近的位置）。

为什么要"向上找"：
    agent 可能从子目录启动。从启动目录一路找到根，第一个 AGENTS.md 生效——
    和 .gitignore / package.json 的查找规则一致，用户不用背。
"""
from __future__ import annotations

from pathlib import Path

GLOBAL_PATH = Path.home() / ".mini_agents" / "AGENTS.md"
MARKER = "AGENTS.md"

# 每层最多多少字节。超了截断——地图写成 100KB 就不是地图了，
# 与其让 agent 读一份过期长文，不如只给前半份并明确告知截断了。
MAX_LAYER_BYTES = 12000


def _read_capped(path: Path) -> str:
    try:
        data = path.read_bytes()[:MAX_LAYER_BYTES]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def find_project_file(start: Path) -> Path | None:
    """从 start 向上找第一份 AGENTS.md（到文件系统根为止）。"""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        hit = candidate / MARKER
        if hit.is_file():
            return hit
    return None


def load(workspace: Path) -> dict:
    """读两层 AGENTS.md，返回拼接结果和验收要用的账目。"""
    layers: list[str] = []
    parts: list[str] = []
    total_bytes = 0

    if GLOBAL_PATH.is_file():
        text = _read_capped(GLOBAL_PATH)
        if text.strip():
            layers.append("global")
            parts.append(f"# 全局约定（{GLOBAL_PATH}）\n\n{text.strip()}")
            total_bytes += GLOBAL_PATH.stat().st_size

    project = find_project_file(workspace)
    if project is not None:
        text = _read_capped(project)
        if text.strip():
            layers.append("project")
            parts.append(f"# 项目约定（{project}）\n\n{text.strip()}")
            total_bytes += project.stat().st_size

    if not parts:
        return {"found": False, "layers": [], "bytes": 0, "text": "", "truncated": False}

    text = "\n\n---\n\n".join(parts)
    return {
        "found": True,
        "layers": layers,
        "bytes": total_bytes,
        "text": text,
        "truncated": total_bytes > 2 * MAX_LAYER_BYTES,
    }


def inject(system_prompt: str, loaded: dict) -> str:
    """把 AGENTS.md 拼进 system prompt。放在最前面：它是"先读地图再上路"。"""
    if not loaded.get("found"):
        return system_prompt
    note = "\n\n（以上约定来自 AGENTS.md，与用户指令冲突时以用户当前指令为准）"
    if loaded.get("truncated"):
        note = "\n\n（注意：AGENTS.md 过长被截断，只加载了前一部分）" + note
    return loaded["text"] + note + "\n\n---\n\n" + system_prompt
