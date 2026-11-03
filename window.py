"""
window.py —— 桌面窗口入口（最终交付形态）

双击这个（打包后是 exe），出来的是一个**有标题栏的原生窗口**，
不是浏览器、不是 localhost 网页。这是现场演示第一考察点。

工作方式：
    1. 先起一个只监听 127.0.0.1 的本地服务（外面访问不到）
    2. 再让 pywebview 开一个系统原生窗口，把本地页面装进去
    3. 关窗口 = 进程退出，不留后台残留

为什么端口要动态找：
    固定 8000 端口，同学机器上很可能被别的程序占着，
    一占就是"打不开"，现场演示直接翻车。所以 bind 0 让系统分配。
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser

import requests
import uvicorn

from server.app import app

TITLE = "Mini Cursor · 我的桌面编程智能体"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until_ready(port: int, timeout: float = 15.0) -> bool:
    """等后端真的能响应再开窗口，否则会先闪一个白屏。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"http://127.0.0.1:{port}/api/health", timeout=0.5).ok:
                return True
        except requests.RequestException:
            time.sleep(0.15)
    return False


def main() -> None:
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    if not wait_until_ready(port):
        print("后端没能在 15 秒内启动，请检查依赖是否装全：pip install -r requirements.txt")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}/"

    try:
        import webview
    except ImportError:
        # 没装 pywebview 时的降级方案：用系统浏览器打开，至少还能用
        print("没装 pywebview，先用浏览器打开。安装桌面窗口：pip install pywebview")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return

    window = webview.create_window(
        TITLE,
        url=url,
        width=1280,
        height=860,
        min_size=(980, 640),
        background_color="#0b0d12",
    )
    webview.start()  # 阻塞在这里，直到窗口被关闭
    server.should_exit = True


if __name__ == "__main__":
    main()
