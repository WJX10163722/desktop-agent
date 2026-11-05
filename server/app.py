"""
server.app —— 本地 WebSocket 服务

它只干三件事：
    1. 把 ui/ 目录的静态文件发出去（页面、样式、脚本）
    2. 收前端发来的 chat / cancel / reset 消息
    3. 把 agent 的每一步事件（开始调工具、工具结果、最终回答）实时推回去

为什么用 WebSocket 而不是普通 HTTP：
    agent 干活要几十秒，HTTP 只能"等结果"，
    而界面上要边跑边看到"正在 read main.py… 成功 0.02s"。
    这就是 trace 面板（评分 5 分）的技术基础。

线程模型（这里最容易写错）：
    agent.run() 是阻塞的，跑在 asyncio.to_thread 开的工作线程里；
    它产生的回调 on_event 发生在**别的线程**，
    必须用 loop.call_soon_threadsafe 把事件塞回事件循环，否则会踩内存。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from contextlib import asynccontextmanager

from agent.config import config
from agent.loop import Agent
from agent.mcp_client import attach_mcp_tools
from agent.tools import build_default_registry

ROOT_DIR = Path(__file__).resolve().parent.parent


def _ui_dir() -> Path:
    """定位界面文件。

    普通 python 运行：就在源码目录的 ui/ 下。
    PyInstaller 打包后：__file__ 指向临时解压目录 _MEIxxxx，
    界面文件是打包时用 --add-data 塞进去的，在 sys._MEIPASS/ui。
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "ui"
    return ROOT_DIR / "ui"


UI_DIR = _ui_dir()

# 全局唯一的工具注册表：本地四工具 + （可选）MCP 外部工具
TOOL_REGISTRY = build_default_registry()
MCP_CLIENT = None
ROUTINE_RUNNER = None


def _start_routines():
    """定时任务（增量 C）：routines.yaml 里每有条目，到点就跑一次技能。

    为什么不在这里 await：定时触发必须独立于界面连接——
    用户没开窗口，任务照样该跑。
    """
    from agent import routines as routines_mod

    items = routines_mod.load(config.workspace)
    if not items:
        return None

    log_dir = Path(config.workspace) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "routines.log"

    def _fire(routine) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] trigger={routine.name} skill={routine.skill}\n"
        try:
            log_file.open("a", encoding="utf-8").write(line)
        except OSError:
            pass
        # 真正跑一次技能：没有 Key 时只记日志，不让定时任务把界面搞崩
        if not config.api_key or not routine.skill:
            return
        try:
            worker = Agent(session_path=None, persist=False)
            worker.run(f"请按 {routine.skill} 技能执行一次，完成后一句话汇报结果。")
        except Exception as e:  # noqa: BLE001
            try:
                log_file.open("a", encoding="utf-8").write(f"  失败：{type(e).__name__}: {e}\n")
            except OSError:
                pass

    runner = routines_mod.RoutineRunner(items, _fire, tick=5.0)
    runner.start()
    return runner


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """服务启动时接一次 MCP server，关闭时断开。

    MCP 失败不影响主程序：attach_mcp_tools 内部吞掉异常，只打印一行提示。
    顺带启动定时任务（增量 C）：工作目录有 routines.yaml 才会真的跑。
    """
    global MCP_CLIENT, ROUTINE_RUNNER
    if os.getenv("AGENT_MCP", "1") != "0":
        MCP_CLIENT = attach_mcp_tools(TOOL_REGISTRY)
    ROUTINE_RUNNER = _start_routines()
    yield
    if ROUTINE_RUNNER is not None:
        ROUTINE_RUNNER.stop()
    if MCP_CLIENT is not None:
        MCP_CLIENT.stop()


app = FastAPI(title="桌面编程智能体", version="0.5.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict:
    """给启动脚本用：窗口打开前先确认后端活着。"""
    return {
        "ok": True,
        "tools": TOOL_REGISTRY.names,
        "mcp": [n for n in TOOL_REGISTRY.names if n.startswith("mcp__")],
        "workspace": str(config.workspace),
        "mock": os.getenv("AGENT_MOCK") == "1",
    }


def create_agent(on_event) -> Agent:
    """建 agent。AGENT_MOCK=1 时用假模型，方便没 Key 也能演示界面。"""
    if os.getenv("AGENT_MOCK") == "1":
        script = [
            {"tool": "read", "args": {"path": "requirements.txt"}, "content": "先看一下依赖。"},
            {"tool": "read", "args": {"path": "不存在的文件.py"}},  # 故意失败，演示兜底
            {"tool": "bash", "args": {"command": "python --version"}},
            "（mock 模式）四个工具都在线，界面链路已跑通。",
        ]
        # mock 模式不落盘：测试跑一次就往 sessions/ 里塞一堆假历史，很脏
        return Agent(
            mock=True, mock_script=script, on_event=on_event, registry=TOOL_REGISTRY, persist=False
        )
    return Agent(on_event=on_event, registry=TOOL_REGISTRY, persist=os.getenv("AGENT_PERSIST", "1") != "0")


@app.websocket("/ws")
async def agent_socket(ws: WebSocket) -> None:
    await ws.accept()

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def on_event(event: dict) -> None:
        # 跨线程投递：agent 在工作线程里跑，不能直接 await
        loop.call_soon_threadsafe(queue.put_nowait, event)

    try:
        agent = create_agent(on_event)
    except Exception as e:  # noqa: BLE001
        await ws.send_json({"type": "error", "message": f"初始化失败：{e}"})
        await ws.close()
        return

    await ws.send_json({
        "type": "ready",
        "tools": agent.registry.names,
        "workspace": str(config.workspace),
        "model": "mock" if os.getenv("AGENT_MOCK") == "1" else config.model,
    })

    # 断点续跑（增量 C）：上次任务被强杀在半路，重启后先把现场捞回来
    resumed_from = agent.resume()
    if resumed_from:
        await ws.send_json({
            "type": "notice",
            "message": f"检测到上次未完成的任务，已从第 {resumed_from} 步恢复现场；接着说一句就能继续。",
        })

    # 上次的历史：关窗口再打开，对话还在（这就是"会话持久化"）
    history = agent.visible_history()
    if history:
        await ws.send_json({"type": "history", "messages": history})

    sender = asyncio.create_task(_sender(ws, queue))
    run_task: asyncio.Task | None = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "消息不是合法 JSON。"})
                continue

            kind = msg.get("type")
            busy = run_task is not None and not run_task.done()

            if kind == "chat":
                text = (msg.get("content") or "").strip()
                if not text:
                    continue
                if busy:
                    # 不排队、不静默丢弃，明确告诉用户为什么没反应
                    await ws.send_json({"type": "error", "message": "上一个任务还在执行，先点停止或等它结束。"})
                    continue
                run_task = asyncio.create_task(_run_chat(agent, text, queue))

            elif kind == "cancel":
                agent.cancel()
                await ws.send_json({"type": "notice", "message": "已发送停止信号，正在中断当前任务…"})

            elif kind == "reset":
                if busy:
                    await ws.send_json({"type": "error", "message": "任务进行中，无法新建会话。"})
                    continue
                agent.reset()
                await ws.send_json({"type": "reset_done"})

            else:
                await ws.send_json({"type": "error", "message": f"未知消息类型：{kind}"})

    except WebSocketDisconnect:
        pass
    finally:
        # 窗口被关掉时，工作线程里的任务也要真的停
        if run_task and not run_task.done():
            agent.cancel()
            run_task.cancel()
        sender.cancel()


async def _run_chat(agent: Agent, text: str, queue: asyncio.Queue) -> None:
    """把阻塞的 agent.run 丢到线程里跑，跑完推一条 done。"""
    started = time.time()
    try:
        result = await asyncio.to_thread(agent.run, text)
        await queue.put({
            "type": "done",
            "answer": result.answer,
            "error": result.error,
            "stop_reason": result.stop_reason,
        })
        # 完成通知（增量 C）：只有跑得久才弹——
        # 用户盯着屏幕时每句都弹托盘，是骚扰不是功能
        if time.time() - started > 20 and os.getenv("AGENT_NOTIFY", "1") != "0":
            from agent.notify import notify

            await asyncio.to_thread(
                notify, "MiniCursor 任务完成", (result.answer or result.error or "")[:60]
            )
    except Exception as e:  # noqa: BLE001
        await queue.put({"type": "done", "answer": "", "error": f"{type(e).__name__}: {e}", "stop_reason": "error"})


async def _sender(ws: WebSocket, queue: asyncio.Queue) -> None:
    """把队列里的事件一条条发给前端。"""
    try:
        while True:
            event = await queue.get()
            await ws.send_json(event)
    except Exception:  # noqa: BLE001 - 连接断了就安静退出
        return
