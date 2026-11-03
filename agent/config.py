"""
agent.config —— 配置加载

为什么单独弄这个文件：
1. Key 只能从环境变量来，不能写死在代码里（泄漏 Key = 该项 0 分）
2. 后面 server / ui / 打包都要用同一份配置，集中一处避免到处 os.getenv
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 项目根目录：
# - 源码运行时 = desktop-agent/
# - PyInstaller 打包后 = exe 所在目录（.env、工作文件都放 exe 旁边）
if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys.executable).resolve().parent
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent


def _env_candidates() -> list[Path]:
    """可能存在 .env 的位置，按优先级：exe 旁边 > 当前目录 > 源码根目录。"""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / ".env")
    candidates.append(Path.cwd() / ".env")
    candidates.append(ROOT_DIR / ".env")
    # 去重并保持顺序
    seen, out = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def load_env(env_file: Path | None = None) -> None:
    """把 .env 文件读进 os.environ。

    手写一个极简解析器，故意不依赖 python-dotenv：
    - 少一个依赖 = 少一个装不上的风险
    - 支持 # 注释、export 前缀、引号包裹
    """
    paths = [env_file] if env_file else _env_candidates()
    found = False
    for path in paths:
        if not path.exists():
            continue
        found = True
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # 已存在的环境变量优先（CI / 系统里配的不被 .env 覆盖）
            os.environ.setdefault(key, value)


class Config:
    """集中存放所有配置项。"""

    def __init__(self) -> None:
        load_env()
        self.api_key: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

        # 安全兜底：循环上限，防止模型反复调工具停不下来
        self.max_turns: int = int(os.getenv("AGENT_MAX_TURNS", "30"))
        self.request_timeout: int = int(os.getenv("AGENT_TIMEOUT", "120"))

        # 失败重试：网络抖动、限流、5xx 都不该让现场演示直接翻车
        self.max_retries: int = int(os.getenv("AGENT_MAX_RETRIES", "3"))

        # 上下文压缩：历史字数超过预算就把中段折成摘要（按字符估算，中文约 1.6 字符/token）
        self.context_budget: int = int(os.getenv("AGENT_CONTEXT_BUDGET", "24000"))
        self.keep_recent: int = int(os.getenv("AGENT_KEEP_RECENT", "8"))

        # 工作目录：agent 能碰到的所有文件都被限制在这个目录里（沙箱）
        self.workspace: Path = Path(os.getenv("AGENT_WORKSPACE", str(ROOT_DIR))).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def require_key(self) -> str:
        """用到真模型时才校验，mock 模式不需要 Key。"""
        if not self.api_key:
            raise RuntimeError(
                "没有找到 DEEPSEEK_API_KEY。\n"
                "  解决：复制 .env.example 为 .env，填入 Key 后重试。\n"
                "  或：加 --mock 参数用假模型验证流程。"
            )
        return self.api_key


config = Config()
