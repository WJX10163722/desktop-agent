# Mini Cursor：桌面智能体课程作业

## 快速开始（三步启动）

```bash
# 1. 克隆仓库
git clone https://github.com/WJX10163722/desktop-agent.git
cd desktop-agent

# 2. 安装依赖
python -m pip install -r requirements.txt

# 3. 配置 Key 并启动
copy .env.example .env      # 打开 .env，填入自己的 DEEPSEEK_API_KEY
python window.py
```

也可以直接下载 Release 中的 `MiniCursor.exe`，双击运行，无需安装 Python。

## 1. 项目简介

Mini Cursor 是一个基于 Python、pywebview 和 DeepSeek 模型接口开发的桌面智能体课程项目，实现了一个具备模型对话能力、本地文件操作能力和工具调用能力的桌面应用（对应作业要求中的“做你自己的 Cursor”）。

项目采用“智能体循环 + 工具注册表”的架构：一个循环负责“思考 → 调用工具 → 读取结果 → 继续思考”，四个本地工具负责真正动手，界面负责把每一步实时展示出来。程序以原生桌面窗口的形式交付，双击即可运行，不依赖浏览器和本地网页地址。

## 2. 主要功能

### 2.1 桌面交互界面

- 原生桌面窗口（pywebview，有标题栏，不是浏览器标签页）
- 对话区：用户提问、模型回答、流式过程提示
- 工具调用 trace 面板：每次调用的工具名、参数、结果、耗时、成功或失败
- 中断按钮：随时停止当前任务
- 状态指示：就绪、运行中、模型未配置等状态

### 2.2 DeepSeek 模型接入

通过 OpenAI 兼容接口调用 DeepSeek 模型，实现自然语言对话与工具调用（function calling）。模型配置通过环境变量读取，包括 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`。

模型接入层位于 `agent/llm.py`，上层只依赖 `chat(messages, tools) -> LLMReply` 这一接口，更换为其他 OpenAI 兼容模型只需改动该文件。模块内另带有 `MockLLM`（假模型），用于在没有 Key、没有网络的情况下跑通全部自测。

### 2.3 本地工具

| 工具 | 功能 |
|---|---|
| `read` | 读取工作区内的文本文件，返回带行号的内容，支持分页、二进制识别、超长单行截断 |
| `write` | 创建或覆盖文件，自动创建父目录，采用临时文件加改名的方式写入 |
| `edit` | 精确字符串替换，必须唯一匹配；多处匹配时报错并列出所在行号，失败不改动文件，成功后返回 diff |
| `bash` | 执行本地命令，返回 stdout、stderr 与 exit_code，包含超时强杀、输出截断、编码兼容与危险命令拦截 |

工具通过 `ToolRegistry` 注册后交给模型，新增工具只需在 `agent/tools/__init__.py` 中注册一行，不需要修改循环代码。

### 2.4 MCP 外部工具

程序启动时通过 MCP（Model Context Protocol）连接一个独立的工具服务 `mcp_servers/workspace_server.py`（官方 SDK 实现，stdio 子进程），自动发现并注册它提供的工具：

| MCP 工具 | 功能 |
|---|---|
| `mcp__workspace__now` | 返回当前日期时间 |
| `mcp__workspace__file_stats` | 统计文件行数、字符数、字节数 |
| `mcp__workspace__list_dir` | 列出目录下的文件与子目录 |

发现到的工具以 `mcp__` 前缀注册进同一张工具表，模型调用方式与本地工具完全一致。

### 2.5 技能系统（按需加载）

`skills/` 下每个子目录是一个技能，由 `SKILL.md` 的 frontmatter（`name` + `description`）与正文组成。启动时**只把目录**（每个技能一行）放进 system prompt，正文要等模型调用 `use_skill(name)` 时才注入——这就是渐进披露。

| 技能 | 形态 | 说明 |
|---|---|---|
| `run-tests` | 纯提示词 | 跑测试、只报失败三要素、不顺手改代码 |
| `review-diff` | 提示词 + `scripts/collect_diff.py` | 先跑脚本拿裁剪过的 diff，再按五条清单审 |
| `project-brief` | 提示词 + MCP 工具 | 编排 `now` / `list_dir` / `file_stats` 生成项目速览 |

新增技能只需建目录写 `SKILL.md`，不改一行代码。`ENABLE_SKILLS=0` 可整体关闭。

### 2.6 定制层

- **AGENTS.md 自动注入**：启动时从工作目录向上找项目级 `AGENTS.md`，再读全局 `~/.mini_agents/AGENTS.md`，按"全局 → 项目"顺序拼进 system prompt；本项目自带的 `AGENTS.md` 是项目地图（构建命令与踩坑），不是文档。
- **三条 hook（代码级硬约束，不是提示词建议）**：
  1. 写 `.env` / `*.pem` / `*.key` → 拒绝，理由"密钥不入库，用环境变量"
  2. `rm -rf` / `git push --force` / `git reset --hard` → 拒绝，并给出替代方案（`git clean -nd`、`git revert`、`git restore`）
  3. `write` / `edit` 成功后自动跑快速测试，失败时只回注"哪条挂了"的摘要（≤3 行），不回注整段 traceback
- **reviewer 子 agent**：`review_code` 工具发起一次只读审查（`read` + 限定 `git` 只读命令），固定五条清单，**只回最终清单**，主上下文增量控制在 500 字以内。
- `ENABLE_HOOKS=0` 可关闭 hook（关闭后不拦截、不自动测试）。

## 3. 安全机制

1. **工作区路径限制**：所有文件工具都会校验目标路径，`../` 等越界访问一律拒绝，避免访问工作目录以外的文件。
2. **编辑唯一匹配**：`edit` 只在目标片段唯一出现时执行，多处匹配直接报错，避免误改；失败时不写入任何内容。
3. **命令超时与拦截**：`bash` 设置单次命令超时，超时后强制终止进程树，并拦截关机、格式化等高风险命令。
4. **输出截断**：命令输出与文件内容过长时间截断，避免上下文被一次性撑爆。
5. **循环次数限制**：单次任务限制最大轮数，防止模型反复调用工具停不下来。
6. **工具参数校验**：检查工具名称与参数字段，非法参数返回错误信息而不是抛出异常。
7. **密钥保护**：通过 `.env` 保存 API Key，并用 `.gitignore` 排除，公开仓库中只保留不含真实密钥的 `.env.example`。
8. **中断机制**：停止信号在每轮思考前和每个工具执行前各检查一次，中断后不会继续执行后续工具。

## 4. 环境要求

- 操作系统：Windows 10 / 11（打包产物为 Windows 可执行文件）
- Python：3.12（项目开发环境）
- 桌面窗口：pywebview
- 服务层：FastAPI + uvicorn（WebSocket 事件推送）
- 模型接口：DeepSeek OpenAI 兼容接口
- 外部工具协议：MCP 官方 Python SDK

## 5. 安装依赖

在项目根目录运行：

```bash
python -m pip install -r requirements.txt
```

## 6. 配置 DeepSeek 模型

1. 将 `.env.example` 复制一份，并命名为 `.env`。
2. 在 `.env` 文件中填写自己的模型配置：

```dotenv
DEEPSEEK_API_KEY=填写自己的API密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

3. 保存配置后启动程序（配置在启动时读取，修改后需重启）。

**安全提示：不要将 `.env`、API Key 或其他个人凭据上传到 GitHub。** 公开仓库中只保留不含真实密钥的 `.env.example`。

## 7. 启动桌面应用

```bash
python window.py
```

启动后会弹出一个深色桌面窗口，出现即表示启动成功。没有 Key 时也可以先用假模型查看界面：

```bash
# Windows PowerShell
$env:AGENT_MOCK="1"; python window.py
```

打包后的独立程序：

```powershell
.\build.ps1        # 产出 dist\MiniCursor.exe
dist\MiniCursor.exe

# 生成提交压缩包（报告 + 演示视频 + 源码 + 程序）
.\make_package.ps1 -Sid 20230001 -Name 张三
```

## 8. 运行自动化测试

```bash
python -m app.selftest        # 一次跑完全部七组，输出 JSON
python tests/test_tools.py    # 工具层
python tests/test_server.py   # 服务层
python tests/test_mcp.py      # MCP 集成
python tests/test_agent.py    # 重试与上下文压缩
python tests/test_session.py  # 会话持久化
python tests/test_skills.py   # 技能加载与渐进披露
python tests/test_custom.py   # hook 拦截、AGENTS.md、审查员隔离
```

测试内容包括工具注册与参数校验、路径沙箱、文件读取分页、写入与覆盖、编辑的唯一匹配与失败不改动、命令超时与输出截断、危险命令拦截、WebSocket 事件流格式、异常输入处理、MCP 握手与工具调用、模型请求重试策略、上下文压缩边界、会话存取与损坏文件容错、技能目录与披露比、hook 拒绝与自动测试红转绿、审查员只读白名单等。

截至本次更新，七组测试共执行 159 项，全部通过（工具层 29、服务层 18、MCP 13、智能体层 16、会话层 22、技能层 21、定制层 40），失败数量为 0。测试不依赖网络与 API Key。正式发布前仍应重新运行测试，并以实际运行结果作为最终测试记录。

## 9. MCP 实现状态

项目已实现真实 MCP 集成，不是占位模块：

- **服务端**：`mcp_servers/workspace_server.py`，使用 MCP 官方 Python SDK 编写，通过 stdio 传输对外提供三个工具。
- **客户端**：`agent/mcp_client.py`，负责启动子进程、完成握手、拉取工具清单、调用工具，并把发现的工具注册进本地工具注册表。
- **协议流程**：`initialize` 握手 → `tools/list` 拉取工具清单 → `tools/call` 调用工具 → 结束后关闭连接，全部为标准 JSON-RPC 消息。
- **打包兼容**：exe 环境没有 Python 解释器，因此 `MiniCursor.exe --mcp-stdio` 会让程序自身以 MCP 服务端方式运行，由主进程作为客户端连接。
- **失败降级**：MCP 连接失败时只打印提示，不影响本地四个工具与界面正常运行。

## 10. 项目目录结构

```text
desktop-agent/
├── window.py                # 桌面窗口入口（pywebview + 动态端口）
├── main.py                  # 命令行调试入口
├── build.ps1                # 一键打包脚本（PyInstaller）
├── make_package.ps1         # 一键生成提交压缩包（报告/视频/源码/程序）
├── requirements.txt         # 项目依赖
├── .env.example             # 环境变量配置模板
├── .gitignore               # Git 忽略规则
├── report.md                # 设计报告（含增量作业三节）
├── AGENTS.md                # 给 agent 看的项目地图（≤300 行，会被单独看）
├── skill_report.json        # 技能系统验收数字（app.inspect 生成）
├── custom_report.json       # 定制层验收数字（app.inspect 生成）
├── agent/                   # 智能体
│   ├── loop.py              # 循环：思考 → 调工具 → 看结果 → 再思考
│   ├── llm.py               # 模型接口与假模型
│   ├── prompts.py           # 系统提示词
│   ├── config.py            # 配置与 .env 加载
│   ├── session.py           # 会话持久化（原子写、坏文件容错）
│   ├── skills.py            # 技能加载器（渐进披露）
│   ├── hooks.py             # PreToolUse / PostToolUse 硬约束
│   ├── agents_md.py         # AGENTS.md 两层注入
│   ├── reviewer.py          # 只读审查子 agent
│   ├── mcp_client.py        # MCP 客户端
│   └── tools/               # 本地工具
│       ├── base.py          # 工具协议与注册表
│       ├── fs.py            # read / write
│       ├── patch.py         # edit
│       └── shell.py         # bash
├── skills/                  # 技能（每个子目录一个 SKILL.md）
│   ├── run-tests/           #   纯提示词
│   ├── review-diff/         #   提示词 + scripts/collect_diff.py
│   └── project-brief/       #   提示词 + MCP 工具编排
├── mcp_servers/
│   └── workspace_server.py  # 独立 MCP 服务端
├── server/
│   └── app.py               # FastAPI + WebSocket 事件推送
├── ui/                      # 界面（index.html / style.css / app.js）
├── app/                     # 验收命令（selftest / inspect）
└── tests/                   # 自动化测试（七组）
```

## 11. 项目当前状态与后续改进

当前版本已实现：桌面窗口与交互界面、DeepSeek 模型对话、四个本地工具、工具调用 trace 展示、任务中断、MCP 外部工具接入、模型请求失败自动重试、上下文自动压缩、会话持久化、技能系统（渐进披露）、定制层（AGENTS.md + 三条 hook + 审查子 agent），并配有 159 项自动化测试。

三个与"能不能当产品用"直接相关的机制：

- **失败重试**：网络异常、超时、429 限流、5xx 服务端错误会退避重试（默认 3 次，`AGENT_MAX_RETRIES` 可调）；4xx（例如 Key 错误）不重试，避免无意义等待。
- **上下文压缩**：历史超过预算（默认 24000 字，`AGENT_CONTEXT_BUDGET`）时，把中段历史折成摘要，保留系统提示与最近若干条；压缩只发生在轮与轮之间，不会拆散工具调用的请求与结果。
- **会话持久化**：历史存于 `sessions/last.json`（已加入 `.gitignore`），关窗口再打开会自动恢复对话；写入采用"临时文件 + 替换"，文件损坏时静默从空会话开始，不会让程序打不开。`AGENT_PERSIST=0` 可关闭。

后续可继续完善：bash 的更严格沙箱隔离、第三方 MCP 服务端接入、多会话切换（目前只保留最近一次），以及 macOS 平台打包。

本项目对已实现功能与尚未实现功能进行区分，不将未完成的部分描述为已完成能力。

## 12. 增量作业：技能系统与定制层

对应标签：`v1.1`（技能系统）、`v1.2`（定制层）。两份验收数字由程序生成，不含手写表格：

```bash
python -m app.selftest                                   # 全量自检（159 项）
python -m app.inspect skills --out skill_report.json     # 技能系统验收
python -m app.inspect custom --out custom_report.json    # 定制层验收
```

| 验收项 | 实测值 | 及格线 |
|---|---|---|
| `disclosure_ratio`（技能正文 / 目录 token 比） | 5.5 | ≥ 5 |
| `trials` 路由实测命中 | 8 / 8（含 2 条"不该触发"） | ≥ 6 条且含反例 |
| `false_positive`（闲聊误触发） | 0 | 必须 0 |
| `agents_md.bytes` | 2446 | ≤ 12000 |
| `denied`（拒绝记录，均带 reason） | 2 条 | ≥ 2 |
| `autotest.red_to_green` | 1（真的红、真的绿） | ≥ 1 |
| `reviewer.main_context_delta` | 340 字 | ≤ 500 |

两个开关（现场验收可直接对比，同一份代码）：

```bash
ENABLE_SKILLS=0 python -m app.inspect skills --mock
ENABLE_HOOKS=0  python -m app.inspect custom --skip-reviewer
```

## 13. 常见问题

- **提示"还没有配置 API Key"**：确认 `.env` 与 `window.py` 在同一目录，且 `DEEPSEEK_API_KEY=` 后面没有多余空格；改完要重启程序。
- **模型回答很慢**：默认超时 120 秒，期间界面显示"运行中"；等不及可以点停止。
- **点停止没反应**：停止信号在工具执行完当前一步后才生效，正在跑的长命令最多再等几秒。
- **打开窗口是白屏**：大概率是界面文件没找到，去 exe 同目录看 `error.log`，把内容发出来排查。
- **换了电脑跑不起来**：确认 Python 3.12 和 `pip install -r requirements.txt`，或者直接用 Release 里的 exe。

## 14. 课程项目说明

本项目用于展示桌面智能体的基础架构设计、模型接口接入、本地工具封装、工具调用安全控制、MCP 协议集成与自动化测试等内容。

- 仓库：<https://github.com/WJX10163722/desktop-agent>
- Release：<https://github.com/WJX10163722/desktop-agent/releases/tag/v1.0.0>

仅用于课程作业展示。
