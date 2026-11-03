"""
agent.tools.fs —— 文件类工具：read / write

这两个工具是"智能体能不能干活"的地基。里面每一行防御都对应一个真实翻车现场：

read:
    - 文件不存在          → 人话报错，并提示用 bash ls 看目录
    - 给的是目录          → 明确说"这是目录"
    - 二进制文件          → 不硬转成乱码，只报大小和类型
    - 文件特别大          → 截断 + 告诉模型怎么翻页（offset / limit）
    - 单行特别长          → 截断单行，避免一个 minified js 把上下文撑爆
    - 行号                → 每行前面带行号，模型要改代码时需要定位

write:
    - 父目录不存在        → 自动创建
    - 写一半断电/崩溃      → 先写临时文件再 os.replace，保证"要么旧要么新"
    - 写空内容            → 允许，但要明确告诉模型"你把文件写空了"
    - 越界路径            → 沙箱拦住，不许跑到工作目录外面
"""
from __future__ import annotations

import os
from pathlib import Path

from ..config import config
from .base import Tool, ToolResult

# 一次最多读多少行 / 单行最多显示多少字符 / 文件大小上限
MAX_LINES_PER_READ = 500
MAX_LINE_CHARS = 2000
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，再大就不适合塞进上下文了


def resolve_path(raw: str) -> Path:
    """把用户/模型给的路径解析成绝对路径，并且不许逃出工作目录。

    这是沙箱。没有它，模型一句 `read ../../.ssh/id_rsa` 就把你私钥读走了。
    """
    if not raw or not str(raw).strip():
        raise ValueError("path 不能为空")

    path = Path(str(raw).strip()).expanduser()
    if not path.is_absolute():
        path = config.workspace / path
    path = path.resolve()

    workspace = config.workspace
    if path != workspace and workspace not in path.parents:
        raise PermissionError(
            f"路径 `{raw}` 在工作目录之外，已拒绝。工作目录是：{workspace}"
        )
    return path


# ---------------------------------------------------------------- read
def read_file(path: str, offset: int = 1, limit: int = 200) -> ToolResult:
    try:
        target = resolve_path(path)
    except (ValueError, PermissionError) as e:
        return ToolResult.failure(str(e))

    if not target.exists():
        return ToolResult.failure(
            f"文件不存在：{target}\n"
            f"如果记不准文件名，可以用 bash 执行 `ls` 看一下当前目录有什么。"
        )
    if target.is_dir():
        return ToolResult.failure(
            f"`{path}` 是一个目录，不是文件。想看目录内容请用 bash 执行 `ls {path}`。"
        )

    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        return ToolResult.failure(
            f"文件太大（{size / 1024 / 1024:.1f} MB），超过 {MAX_FILE_BYTES // 1024 // 1024} MB 上限，不整篇读取。\n"
            f"建议：用 bash 的 grep/head/tail 定位，或者分段读。"
        )

    raw = target.read_bytes()
    if b"\x00" in raw[:8192]:
        return ToolResult.failure(
            f"`{path}` 看起来是二进制文件（{size} 字节），不按文本展示。"
        )

    # errors="replace"：Windows 上的 GBK 文件、混合编码都不会直接崩
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()

    total = len(lines)
    start = max(1, int(offset))
    count = max(1, min(int(limit), MAX_LINES_PER_READ))
    end = min(total, start + count - 1)

    if start > total:
        return ToolResult.failure(
            f"offset={start} 超过文件总行数 {total}。文件一共 {total} 行。"
        )

    body = []
    for i in range(start - 1, end):
        line = lines[i]
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + f" …（本行共 {len(line)} 字符，已截断）"
        body.append(f"{i + 1:>5}| {line}")

    header = f"{target}（共 {total} 行，当前 {start}-{end} 行）"
    footer = ""
    if end < total:
        footer = f"\n[还有 {total - end} 行未显示，用 offset={end + 1} 继续读]"

    return ToolResult.success(
        "\n".join([header, *body]) + footer,
        path=str(target), start=start, end=end, total_lines=total,
    )


# ---------------------------------------------------------------- write
def write_file(path: str, content: str) -> ToolResult:
    try:
        target = resolve_path(path)
    except (ValueError, PermissionError) as e:
        return ToolResult.failure(str(e))

    if target.exists() and target.is_dir():
        return ToolResult.failure(f"`{path}` 是个目录，不能当文件写。")

    if content is None:
        content = ""
    if not isinstance(content, str):
        content = str(content)

    existed = target.exists()
    old_lines = 0
    if existed:
        try:
            old_lines = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            old_lines = 0

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # 原子写：先写同目录临时文件，再一步替换。断电也不会留下半个文件。
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(content, encoding="utf-8", newline="\n")
        os.replace(tmp, target)
    except OSError as e:
        return ToolResult.failure(f"写入失败（权限或磁盘问题）：{e}")

    new_lines = len(content.splitlines())
    action = "覆盖" if existed else "新建"
    note = ""
    if not content:
        note = "\n注意：写入内容为空，文件现在是空的（0 字节）。"
    if existed:
        note += f"\n变化：{old_lines} 行 → {new_lines} 行。"

    return ToolResult.success(
        f"已{action} {target}（{new_lines} 行，{len(content.encode('utf-8'))} 字节）。{note}",
        path=str(target), existed=existed, lines=new_lines,
    )


# ---------------------------------------------------------------- 工具声明
tool_read = Tool(
    name="read",
    description=(
        "读取一个文本文件的完整内容，返回带行号的文本。"
        "在你需要看代码、看配置、确认文件现状时使用。"
        "不要凭记忆猜测文件内容，要看就看。"
        "文件很长时用 offset/limit 分页读，一次不要读超过 200 行。"
        "图片等二进制文件不要用这个工具。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径，相对工作目录（如 main.py）或绝对路径均可。必须是文件，不能是目录。",
            },
            "offset": {
                "type": "integer",
                "description": "从第几行开始读，默认 1（文件第一行）。只有分页读取时才需要传。",
            },
            "limit": {
                "type": "integer",
                "description": "最多读多少行，默认 200，最大 500。",
            },
        },
        "required": ["path"],
    },
    func=read_file,
)

tool_write = Tool(
    name="write",
    description=(
        "把内容写入文件：文件不存在就新建，存在就整体覆盖。"
        "写新文件、或者确实需要整篇重写时用它。"
        "只改文件里的一小处时，不要用 write（会丢掉其他内容），用 edit。"
        "父目录不存在会自动创建。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要写入的文件路径，相对工作目录或绝对路径。",
            },
            "content": {
                "type": "string",
                "description": "要写入的完整文件内容。必须是完整内容，不是片段。",
            },
        },
        "required": ["path", "content"],
    },
    func=write_file,
)
