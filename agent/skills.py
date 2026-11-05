"""
agent.skills —— 技能系统（增量 A）

一句话：技能是「按需加载的流程说明书」，不是工具，也不是提示词碎片。

核心考点是渐进披露（progressive disclosure）：
    启动时只把目录塞给模型（每个技能一行 name + description），
    正文只有在模型明确调 use_skill(name) 时才进上下文。

    20 个技能全塞 = 约 2 万 token，每轮都在；
    只放目录 = 约 800 token。
    这笔账不是省 token 省钱，是省注意力——目录越短，模型越不容易看花眼。

为什么不用 pyyaml 解析 frontmatter：
    SKILL.md 的头部只有 name/description 两个键，手写 10 行解析器
    比引入一个依赖（再传染一层打包问题）划算得多。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT_DIR

SKILLS_DIR = ROOT_DIR / "skills"

# 中文场景下约 1.6 个字符折一个 token。不追求精确（各模型分词不同），
# 但必须全程用同一把尺子，report 里的对比数字才有意义。
_CHARS_PER_TOKEN = 1.6


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / _CHARS_PER_TOKEN))


@dataclass
class Skill:
    """一个技能 = 目录名 + frontmatter 元数据 + 正文。"""
    name: str
    description: str
    body: str
    path: Path

    @property
    def has_scripts(self) -> bool:
        return (self.path.parent / "scripts").is_dir()

    @property
    def uses_mcp(self) -> bool:
        return "mcp__" in self.body

    @property
    def full_text(self) -> str:
        return f"# {self.name}\n\n{self.body}"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """把 `---\\nkey: value\\n---\\n正文` 拆成（元数据，正文）。

    宽容一点：没有 frontmatter 就当空元数据，方便手写技能时不被格式卡死。
    """
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            meta[key] = value
    return meta, text[match.end():]


class SkillLoader:
    """扫描 skills/ 目录，提供目录（catalog）和按需加载。"""

    def __init__(self, skills_dir: Path | str | None = None) -> None:
        self.dir = Path(skills_dir) if skills_dir else SKILLS_DIR
        self.skills: dict[str, Skill] = {}
        self.errors: list[str] = []
        self._scan()

    def _scan(self) -> None:
        self.skills.clear()
        self.errors.clear()
        if not self.dir.is_dir():
            return
        for md in sorted(self.dir.glob("*/SKILL.md")):
            try:
                meta, body = _parse_frontmatter(md.read_text(encoding="utf-8"))
            except OSError as e:
                self.errors.append(f"{md}: {e}")
                continue
            name = meta.get("name") or md.parent.name
            description = meta.get("description", "")
            if not description:
                self.errors.append(f"{md}: 缺 description（没有它模型永远不知道什么时候用）")
                continue
            self.skills[name] = Skill(name=name, description=description, body=body.strip(), path=md)

    # ---------- 查询 ----------
    def __len__(self) -> int:
        return len(self.skills)

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def names(self) -> list[str]:
        return list(self.skills)

    def catalog_lines(self) -> list[str]:
        """目录：每个技能一行。这是唯一常驻上下文的部分。"""
        return [f"- {s.name}: {s.description}" for s in self.skills.values()]

    def catalog_text(self) -> str:
        return "\n".join(self.catalog_lines())

    # ---------- 数字（inspect 用，全程同一把尺子） ----------
    def catalog_tokens(self) -> int:
        return estimate_tokens(self.catalog_text())

    def full_tokens(self) -> int:
        return sum(estimate_tokens(s.full_text) for s in self.skills.values())

    def summary(self) -> list[dict]:
        return [
            {"name": s.name, "has_scripts": s.has_scripts, "uses_mcp": s.uses_mcp}
            for s in self.skills.values()
        ]
