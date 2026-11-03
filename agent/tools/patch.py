"""
agent.tools.patch —— edit 工具：精确字符串替换

为什么这件事这么难，值得单独一个文件：
    模型改代码最容易犯的错，是"以为自己改的是那一处，其实改了一大片"。
    edit 的全部设计目标就是：**不确定的地方，宁可报错，也不许猜。**

四条铁律（评分表里 edit 做成整文件重写 = 该项 0 分）：
    1. old_string 必须在文件里**唯一出现**；出现 2 次以上直接报错，绝不"帮你选第一个"
    2. old_string 为空直接拒绝（空字符串能匹配任何位置，等于全文污染）
    3. 找到 0 次时报错，并且**尝试诊断**是不是缩进/空白不一致导致的
    4. 写入是原子的：要么整篇换成新的，要么原文件一个字不动 —— 这就是"能回滚"
"""
from __future__ import annotations

import difflib
import os

from .base import Tool, ToolResult
from .fs import resolve_path

MAX_DIFF_LINES = 40


def _diagnose_not_found(text: str, old: str) -> str:
    """找不到时给点有用的线索，而不是干巴巴一句"未找到"。"""
    hints = []
    if old.strip() and old.strip() in text:
        hints.append("内容其实存在，但**首尾空白或缩进**跟文件不一致。请连着缩进一起复制。")
    elif old.strip() and old.strip() in text.replace("\r\n", "\n"):
        hints.append("文件是 CRLF 换行，你给的是 LF。试着少给几行、只给关键一行。")
    if len(old.splitlines()) > 6:
        hints.append("old_string 太长，越长的片段越容易有一两个字符对不上。建议只取 2~3 行。")
    return "\n".join(hints)


def _unified_diff(old_text: str, new_text: str, path: str) -> str:
    diff = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
            n=2,
        )
    )
    if not diff:
        return "（内容没有实际变化）"
    if len(diff) > MAX_DIFF_LINES:
        diff = diff[:MAX_DIFF_LINES] + [f"... 差异过长，仅显示前 {MAX_DIFF_LINES} 行"]
    return "\n".join(diff)


def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> ToolResult:
    try:
        target = resolve_path(path)
    except (ValueError, PermissionError) as e:
        return ToolResult.failure(str(e))

    if not target.exists():
        return ToolResult.failure(f"文件不存在：{target}。新建文件请用 write。")
    if target.is_dir():
        return ToolResult.failure(f"`{path}` 是目录，不能编辑。")

    if not isinstance(old_string, str) or old_string == "":
        return ToolResult.failure("old_string 不能为空。要整篇重写请用 write，要新增内容请给一段具体的旧文本。")

    try:
        original = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ToolResult.failure(f"`{path}` 不是 UTF-8 文本（可能是二进制或 GBK 编码），edit 不支持，请用 write 重写。")
    except OSError as e:
        return ToolResult.failure(f"读取失败：{e}")

    count = original.count(old_string)

    if count == 0:
        hint = _diagnose_not_found(original, old_string)
        return ToolResult.failure(
            f"在 `{path}` 里找不到这段文本。\n"
            f"你给的片段（前 200 字）：\n{old_string[:200]}\n"
            + (f"\n可能的原因：\n{hint}" if hint else "\n建议：先用 read 看一眼真实内容，再原样复制。")
        )

    if count > 1 and not replace_all:
        line_numbers = []
        cursor = 0
        while True:
            idx = original.find(old_string, cursor)
            if idx == -1:
                break
            line_numbers.append(original.count("\n", 0, idx) + 1)
            cursor = idx + len(old_string)
        return ToolResult.failure(
            f"这段文本在 `{path}` 里出现了 {count} 次（第 {', '.join(map(str, line_numbers))} 行）。\n"
            f"edit 拒绝猜测你想改哪一处。请任选一种做法：\n"
            f"  1. 多带几行上下文，让这段文本变成唯一的\n"
            f"  2. 如果你确实要全部替换，把 replace_all 设为 true"
        )

    if old_string == new_string:
        return ToolResult.success(f"没有变化：old_string 与 new_string 相同，`{path}` 未改动。")

    updated = original.replace(old_string, new_string) if replace_all else original.replace(old_string, new_string, 1)

    try:
        tmp = target.with_name(target.name + ".edit.tmp")
        tmp.write_text(updated, encoding="utf-8", newline="\n")
        os.replace(tmp, target)
    except OSError as e:
        # 原子写失败时原文件仍完好，天然回滚
        return ToolResult.failure(f"写入失败（原文件未改动）：{e}")

    replaced = count if replace_all else 1
    return ToolResult.success(
        f"已在 `{path}` 替换 {replaced} 处。\n\n{_unified_diff(original, updated, path)}",
        path=str(target), replaced=replaced,
        diff=_unified_diff(original, updated, path),
    )


tool_edit = Tool(
    name="edit",
    description=(
        "精确替换文件里的一段文本：把 old_string 换成 new_string。"
        "修改已有文件里的局部代码时，优先用它，不要用 write 整篇重写。"
        "old_string 必须从文件里原样复制，且必须在文件中唯一出现；"
        "如果需要同时替换全部出现的位置，把 replace_all 设为 true。"
        "替换失败时文件不会被修改，可以放心重试。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要修改的文件路径。"},
            "old_string": {
                "type": "string",
                "description": "被替换的原文，必须与文件内容逐字符一致（含缩进），并且在文件中唯一。",
            },
            "new_string": {
                "type": "string",
                "description": "替换后的新文本。注意保留原有缩进风格。",
            },
            "replace_all": {
                "type": "boolean",
                "description": "是否替换全部匹配项，默认 false（只允许唯一匹配）。",
            },
        },
        "required": ["path", "old_string", "new_string"],
    },
    func=edit_file,
)
