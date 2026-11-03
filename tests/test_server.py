"""
服务层自测（不联网、不花钱、不需要 Key）

跑法：
    python tests/test_server.py

验证的是"界面拿到的数据"这一层：
    1. /api/health 能告诉窗口后端是否活着、装了哪些工具
    2. WebSocket 能把工具调用事件一条条实时推出去（trace 面板的数据源）
    3. 工具失败时事件里 ok=False，界面能显示成红色失败卡片
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["AGENT_MOCK"] = "1"  # 必须在导入 server.app 之前设置

import requests  # noqa: E402
import uvicorn  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

from server.app import app  # noqa: E402

passed = 0
failed = 0


def check(title: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {title}")
    else:
        failed += 1
        print(f"  FAIL  {title} {detail}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if requests.get(f"{base}/api/health", timeout=0.5).ok:
                break
        except requests.RequestException:
            time.sleep(0.15)

    print("\n[1] HTTP 与静态资源")
    health = requests.get(f"{base}/api/health", timeout=3).json()
    check("health 返回 ok", health.get("ok") is True)
    check("四个工具都在", {"read", "write", "edit", "bash"} <= set(health.get("tools", [])), health.get("tools"))
    check("首页能拿到", requests.get(base, timeout=3).status_code == 200)
    check("静态样式能拿到", requests.get(f"{base}/static/style.css", timeout=3).status_code == 200)
    check("前端脚本能拿到", requests.get(f"{base}/static/app.js", timeout=3).status_code == 200)

    print("\n[2] WebSocket 事件流")
    events = []
    with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5) as ws:
        ready = json.loads(ws.recv(timeout=5))
        check("连上先收到 ready", ready["type"] == "ready", ready)
        check("ready 里带模型信息", ready.get("model") == "mock", ready.get("model"))

        ws.send(json.dumps({"type": "chat", "content": "演示一下"}))

        while True:
            ev = json.loads(ws.recv(timeout=20))
            events.append(ev)
            if ev["type"] == "done":
                break

    kinds = [e["type"] for e in events]
    check("推送了 tool_start", "tool_start" in kinds, kinds)
    check("推送了 tool_end", "tool_end" in kinds, kinds)
    check("最后有 done", kinds[-1] == "done", kinds)

    starts = [e for e in events if e["type"] == "tool_start"]
    ends = [e for e in events if e["type"] == "tool_end"]
    check("走了三个工具调用", len(starts) == 3 and len(ends) == 3, (len(starts), len(ends)))
    check("工具名对得上", [e["name"] for e in starts] == ["read", "read", "bash"], [e["name"] for e in starts])

    bad = [e for e in ends if not e["ok"]]
    check("失败的工具标记为 ok=False", len(bad) == 1 and "文件不存在" in bad[0]["content"], bad[:1])
    check("每个工具都有耗时", all("elapsed" in e for e in ends))

    done = events[-1]
    check("done 带最终回答", bool(done.get("answer")) and done.get("error") is None, done)

    print("\n[3] 异常输入不许把服务打挂")
    with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5) as ws:
        ws.recv(timeout=5)
        ws.send("这不是 JSON")
        ev = json.loads(ws.recv(timeout=5))
        check("非 JSON 消息返回 error 而不是崩", ev["type"] == "error", ev)

        ws.send(json.dumps({"type": "不存在的类型"}))
        ev = json.loads(ws.recv(timeout=5))
        check("未知消息类型返回 error", ev["type"] == "error", ev)

        ws.send(json.dumps({"type": "chat", "content": "  "}))
        ws.send(json.dumps({"type": "chat", "content": "跑一下"}))
        first = json.loads(ws.recv(timeout=10))
        check(
            "空消息被忽略、后续消息照常处理",
            first["type"] in {"user_message", "tool_start", "assistant_message"},
            first,
        )

    server.should_exit = True
    print(f"\n结果：{passed} 通过，{failed} 失败")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
