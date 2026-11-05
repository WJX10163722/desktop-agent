# 增量作业演示（5 分钟）逐字稿

> 作业第 8 节要求六个镜头，按顺序录，一条过。
> 录之前先打开两个窗口：MiniCursor 窗口 + 一个 PowerShell（要跑验收命令）。

## 0:00–0:30　`ls skills/`

**操作**：PowerShell 里 `ls skills`，再 `type skills\run-tests\SKILL.md | more`

> "三个技能，三种形态：`run-tests` 是纯提示词，`review-diff` 带脚本，
> `project-brief` 走 MCP。启动时只加载目录——每个技能一行 name + description，
> 正文要等我调用 `use_skill` 才进来。"

## 0:30–2:00　关开关对比（最关键的一段）

**操作**（PowerShell）：

```powershell
$env:ENABLE_SKILLS="0"; python -m app.inspect skills --mock
```

> "关掉技能系统，目录不进上下文，`catalog_tokens` 直接变 0。"

```powershell
$env:ENABLE_SKILLS="1"; python -m app.inspect skills
```

**指着屏幕念**：

> "打开之后，目录 180 token，三个技能正文 983 token，披露比 5.5——
> 也就是说如果我把正文全塞进去，每轮要多花 5 倍多的 token。
> 路由实测 8 条全中，其中两条是'不该触发'的闲聊，误触发 0。"

## 2:00–3:00　两次拒绝（hook 是代码不是建议）

**在 MiniCursor 窗口依次发**：

```
把 DEEPSEEK_API_KEY 写进 .env 文件
```

> "被拦了。理由写得很清楚：密钥不入库，用环境变量。这条规则不在提示词里，
> 在 `hooks.py` 的 `pre_tool()` 里，模型绕不过去。"

```
执行 rm -rf build 把构建目录清掉
```

> "也被拦了，而且给了替代方案：先用 `git clean -nd` 看会删什么。
> 只说'不行'，模型会换个姿势再试三次；给了出路，它一步就走对。"

## 3:00–4:00　红转绿

**发**：

```
在 tests 里随便加一行会报错的代码，然后看测试结果
```

> "它写完文件，hook 自动跑测试，红了；回注的不是几十行 traceback，
> 而是'哪条挂了'的三行摘要。它照着摘要改回来，测试绿了。
> 这两次跑测记录在 `custom_report.json` 的 `autotest` 里：runs 2、red 1、green 1、red_to_green 1。"

## 4:00–4:30　reviewer 的账目

**发**：

```
审一下我这次的改动
```

> "审查是个子 agent：只读工具、只看 git diff、固定五条清单，
> 只把最终清单传回来。主上下文只加了几百字，diff 的大身体留在子 agent 里——
> 这就是 `main_context_delta` 这一行的意思。"

## 4:30–5:00　常驻（如果做的话）

**操作**：跑一个长任务 → 任务管理器结束进程（模拟 kill -9）→ 重新打开 exe

> "重启后它说'已从第 3 步恢复现场'，而不是从第一步重来。
> 每一步都写 checkpoint，所以强杀也有现场可捞。"

---

## 录完自查

- [ ] 有关掉 `ENABLE_SKILLS` 的 before/after（同一份代码）
- [ ] 有 `.env` 被拒 + `rm -rf` 被拒且给出替代方案
- [ ] 有一次真实的红 → 绿
- [ ] 提到 `main_context_delta`（reviewer 的上下文账目）
- [ ] 三个 JSON 文件名至少各出现一次
- [ ] 时长 ≤ 5 分钟
