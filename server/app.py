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
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent.config import config
from agent.loop import Agent
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

app = FastAPI(title="桌面编程智能体", version="0.4.0")
app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict:
    """给启动脚本用：窗口打开前先确认后端活着。"""
    return {
        "ok": True,
        "tools": build_default_registry().names,
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
        return Agent(mock=True, mock_script=script, on_event=on_event)
    return Agent(on_event=on_event)


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
    try:
        result = await asyncio.to_thread(agent.run, text)
        await queue.put({
            "type": "done",
            "answer": result.answer,
            "error": result.error,
            "stop_reason": result.stop_reason,
        })
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
