# Mini Cursor · 我的桌面编程智能体

一个带**原生桌面窗口**的迷你编程智能体：一个循环 + 一个模型接口 + **四个工具**
（`read` / `write` / `edit` / `bash`），能真正读写你的文件、跑命令、修 bug。
课堂作业《Python 智能体专题：做你自己的 Cursor》的交付项目。

## Quick Start（三步启动）

```bash
# 1. 克隆仓库
git clone https://github.com/WJX10163722/desktop-agent.git
cd desktop-agent

# 2. 装依赖
pip install -r requirements.txt

# 3. 配置 Key 并启动
copy .env.example .env      # 打开 .env，填入你的 DEEPSEEK_API_KEY
python window.py
```

弹出深色窗口即启动成功。没有 Key 也可以先看界面：

```bash
# Windows PowerShell
$env:AGENT_MOCK="1"; python window.py
```

## 它能干什么

- **read**：读文件（带行号、自动分页、二进制识别、超长单行截断）
- **write**：新建/覆盖文件（自动建父目录、原子写入）
- **edit**：精确替换（必须唯一匹配，多处出现直接报错，失败不改动文件，返回 diff）
- **bash**：执行命令（stdout/stderr/exit_code、超时强杀、输出截断、危险命令拦截）

所有文件操作被限制在工作目录沙箱内，`../` 逃逸一律拒绝。

## 目录结构

```
desktop-agent/
├── window.py            # 桌面窗口入口（pywebview + 动态端口）
├── main.py              # 命令行调试入口
├── agent/
│   ├── loop.py          # agent 循环：思考 → 调工具 → 看结果 → 再思考
│   ├── llm.py           # 模型适配层（OpenAI 兼容协议，可换模型）
│   ├── prompts.py       # 系统提示词
│   ├── config.py        # 配置与 .env 加载
│   └── tools/
│       ├── base.py      # 工具协议与注册表（失败即消息，异常即兜底）
│       ├── fs.py        # read / write
│       ├── patch.py     # edit
│       └── shell.py     # bash
├── server/app.py        # FastAPI + WebSocket：把每一步实时推给界面
├── ui/                  # 界面（零框架：index.html + style.css + app.js）
└── tests/               # 自测：工具层 29 条 + 服务层 18 条
```

## 设计上比较在意的地方

- **分层**：`agent/` 不知道界面的存在，`ui/` 不知道模型的存在，中间只走 WebSocket 事件
- **失败是消息不是异常**：工具报错返回一句"人话 + 下一步建议"，模型能自己纠正，循环永不崩
- **中断是真停**：停止信号在下一轮思考前、每个工具执行前各检查一次，不是假装停
- **trace 可见**：每次工具调用的参数、结果、耗时实时摊开在右侧面板

## 自测

```bash
python tests/test_tools.py    # 工具层：注册/兜底/分页/沙箱/edit/bash
python tests/test_server.py   # 服务层：HTTP/WebSocket 事件流/异常输入（内置 mock 模型）
```

## 打包

```powershell
.\build.ps1        # 产出 dist\MiniCursor.exe
```

## License

仅用于课程作业展示。
