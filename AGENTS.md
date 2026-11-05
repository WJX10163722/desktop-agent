# 项目地图

agent/     ← 核心：loop.py 是 agent 循环（模型↔工具往返），skills.py 技能加载，hooks.py 硬约束，reviewer.py 审查子 agent
agent/tools/  ← 四工具实现（fs / patch / shell）+ base.py 工具协议。改工具行为只动这里，loop.py 不用碰
server/    ← FastAPI + WebSocket：把 agent 循环接到界面，app.py 一个文件搞定
ui/        ← 单页界面（原生 HTML/JS，无构建步骤），改完直接刷新窗口就能看
mcp_servers/ ← 自研 MCP server（stdio 子进程），提供 now / list_dir / file_stats 三个外部工具
app/       ← 验收命令（selftest / inspect），只依赖 agent/ 层，不碰 server 和 ui
tests/     ← 六组自测脚本，每组独立可跑、退出码 0/1
skills/    ← 技能目录，每个子目录一个 SKILL.md（frontmatter：name + description 必填）

# 构建与测试

python -m app.selftest        # 全量自检（119 项，约 40 秒，不依赖网络）
python tests/test_tools.py    # 快速冒烟（29 项，约 3 秒，改完工具先跑这个）
powershell -File build.ps1    # PyInstaller 打包出 dist\MiniCursor.exe（noconsole）

# 代码风格

- 只用标准库 + fastapi/uvicorn/requests/pyinstaller，不引新依赖（pyyaml 也不行，frontmatter 手写解析）
- 工具函数返回 ToolResult，失败用 ToolResult.failure，不抛异常出工具层
- agent 事件统一走 _emit，界面别直接碰 agent 内部状态
- 中文注释，注释写"为什么"，不写"是什么"

# 坑

- 这是 Python 3.12：`X | None` 直接写，不要 import Optional
- 打包是 --noconsole：sys.stdout 是 None，任何 print 都会炸——窗口进程里禁止 print，排障用 error.log（window.py 的 excepthook 会写）
- uvicorn.Config 必须传 log_config=None，否则它的彩色日志格式化器碰 stdout 直接崩（step8 踩过）
- MockLLM 剧本一轮一条：字符串=直接回答，{"tool":..,"args":..}=发起工具调用；测试全靠它，别删
- 上下文压缩只在轮与轮之间做，绝不能拆散 assistant(tool_calls) 和 tool 消息的配对，拆了接口直接 400
- server 的 TOOL_REGISTRY 是多连接共享的：往里注册工具前先查重，否则第二个连接就 ValueError
- 路径沙箱以 config.workspace 为根，所有文件路径必须过 tools 里的 resolve，自己 os.path.join 出来的路径不经检查
