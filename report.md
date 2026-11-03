# report.md —— Mini Cursor 设计报告

项目：**Mini Cursor · 我的桌面编程智能体**
仓库：<https://github.com/WJX10163722/desktop-agent>
Release：<https://github.com/WJX10163722/desktop-agent/releases/tag/v1.0.0>

---

## 一、做了什么

一个带**原生桌面窗口**的迷你编程智能体：一个循环 + 一个模型接口（DeepSeek，OpenAI 兼容协议）
+ 四个本地工具（`read` / `write` / `edit` / `bash`）+ 三个 MCP 外部工具，
能真正读写文件、跑命令、改代码、报错时自己纠正。双击 `MiniCursor.exe` 即用，不需要 Python 环境。

## 二、架构与设计取舍

```
┌─────────────── 界面层（不认识模型） ───────────────┐
│ window.py   pywebview 原生窗口（有标题栏，不是浏览器）│
│ ui/         零框架三件套：index.html/style.css/app.js│
└────────────────────┬─────────────────────────────┘
                     │ WebSocket 事件流（tool_start/tool_end/done…）
┌────────────────────┴─────────────────────────────┐
│ server/app.py  FastAPI：跨线程投递、取消信号、健康检查│
└────────────────────┬─────────────────────────────┘
┌────────────────────┴─────────────────────────────┐
│ agent/（不认识界面）                               │
│  loop.py    循环：思考→调工具→看结果→再思考         │
│  tools/     ToolRegistry：工具是注册进来的，不是 if-else│
│  llm.py     模型适配层 + MockLLM（无 Key 可跑全部测试）│
│  mcp_client.py  MCP 客户端（官方 SDK，stdio 子进程）  │
└──────────────────────────────────────────────────┘
```

三个刻意的取舍：

1. **分层隔离**：`agent/` 不知道界面的存在，`ui/` 不知道模型的存在。
   换 Electron / 换 GPT / 换 Ollama，各自只改一个文件。
2. **失败是消息，不是异常**：工具报错返回"人话 + 下一步建议"，
   模型下一轮自己纠正（演示中真实发生：read 不到文件 → 自己改用 bash 读）。
3. **MockLLM 贯穿始终**：60 条自测全部不需要 API Key、不需要网络。

## 三、工具层要点（对应评分 22 分）

| 工具 | 关键设计 |
|------|----------|
| read | 行号返回、分页（默认 800 行）、二进制识别、超长单行截断 |
| write | 自动建父目录、原子写（临时文件+rename）、禁止空路径 |
| edit | 精确唯一匹配；多处匹配**拒绝猜测**并列出行号；失败**不改动文件**；返回 unified diff |
| bash | 超时强杀进程树（taskkill /T）、输出头尾截断、UTF-8/GBK 编码回退、危险命令拦截 |

所有文件操作限制在工作目录沙箱内，`../` 逃逸直接拒绝（bash 命令本身除外，见"五、最不满意"）。

## 四、踩过的坑（全部真实发生，修复记录见 git log）

1. **bash 静默失败**：`Popen(["cmd","/c",'python -c "print(1/0)"'])` 传列表时
   Python 按 MSVCRT 规则加引号，`cmd /c` 解析错位 → exit 0、无输出。
   修复：整串命令 `shell=True` 交给 shell。**这是"工具在骗我"的典型，值得讲。**
2. **打包后找不到界面**：PyInstaller onefile 运行时解压到 `_MEIxxxx` 临时目录，
   `__file__` 相对定位失效。修复：`--add-data ui;ui` + `sys._MEIPASS` 定位。
3. **noconsole 崩溃**：uvicorn 默认日志配置要摸 `sys.stdout`（打包后是 `None`）
   → AttributeError。修复：`log_config=None`；并增加 `error.log` 落盘兜底。
4. **MCP 子进程环境**：Windows 上给子进程只传自定义变量会缺 `SYSTEMROOT`，
   python 无法初始化。修复：必须继承 `{**os.environ, ...}`。
5. **MCP SDK 2.x 改名**：`inputSchema`→`input_schema`、`isError`→`is_error`，做了双兼容。
6. **anyio 异常组套娃**：真实错误被 `ExceptionGroup` 层层包裹，
   写了递归拆解 `_explain()`，否则用户永远只看到 "unhandled errors in a TaskGroup"。
7. **exe 里没有 python**：MCP server 无法 `python server.py` 启动。
   修复：`MiniCursor.exe --mcp-stdio` 让 exe 自充当 server（实测可握手、可调用）。

## 五、最不满意的地方（诚实版）

1. **bash 没有真沙箱**：文件工具的路径有沙箱，但 bash 命令本身可以用
   `..\` 访问工作目录外（Windows 上也没有干净的 namespace 隔离手段）。
   目前的缓解是危险命令黑名单 + 超时，理论上更完备的方案是 Windows Sandbox / Job Object。
2. **会话不持久**：关窗口上下文就没了（PPT 提到会话持久化是加分项，本次未做）。
3. **上下文不压缩**：长对话会线性增长 token，没做摘要压缩（同样是加分项未做）。
4. **MCP server 是自己写的**：协议是真协议（官方 SDK、标准 JSON-RPC），
   但工具比较简单；接一个第三方 GitHub/文件系统 server 是下一步方向。

## 六、自测

```
python tests/test_tools.py    29 条：注册/兜底/分页/沙箱/edit 五情况/bash 五情况
python tests/test_server.py   18 条：HTTP/WebSocket 事件流/异常输入（内置 mock）
python tests/test_mcp.py      13 条：握手/tools list/tools call/沙箱/注册表集成
python tests/test_agent.py    16 条：重试策略（网络/401/5xx）与上下文压缩边界
────────────────────────────────
合计 76 条，全部不依赖网络与 API Key
```

## 六之二、稳定性两个机制

1. **失败重试**：网络异常、超时、429、5xx 退避重试（默认 3 次）；4xx 不重试。
   演示现场网络抖动时，这条决定你是"等两秒继续"还是"红字道歉"。
2. **上下文压缩**：超过预算（默认 24000 字）把中段折成摘要，保留 system + 最近 8 条。
   只发生在轮与轮之间，绝不会拆散 assistant(tool_calls) 与 tool 结果的配对——
   这个配对一旦被截断，接口直接 400，是我们踩过的坑。

## 七、现场演示脚本（5 分钟）

1. 双击 `MiniCursor.exe` → 原生窗口弹出（标题栏 Mini Cursor）
2. `读一下 main.py，告诉我它是干什么的` → trace 面板实时出现工具卡片
3. `读一下 不存在的文件.py` → 红色失败卡片 + 模型自愈（不装人）
4. `在 demo 文件夹建 notes.txt 写三行内容并用命令打印` → write + bash 连击
5. `现在几点了？统计 main.py 行数` → 三张 `mcp__workspace__*` 卡片（MCP 调用成功）
6. 中途点停止 → 立刻中断，出现"已中断"提示（中断是真停）
7. 说最不满意的地方：bash 沙箱与上下文压缩（见第五节）

## 八、运行方式（三步启动）

```bash
git clone https://github.com/WJX10163722/desktop-agent.git
cd desktop-agent
pip install -r requirements.txt
copy .env.example .env   # 填入 DEEPSEEK_API_KEY
python window.py
```

或直接下载 Release 里的 `MiniCursor.exe` 双击运行。
