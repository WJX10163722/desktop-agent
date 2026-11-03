"""
main.py —— 命令行入口

现在的形态（D2）：命令行 + 四个工具里的 read/write，用来验证后端逻辑。
最终形态（D4 之后）：这个 CLI 保留当调试口，
   真正的产品是 server + 桌面窗口。

常用命令：
    python main.py --mock --demo          # 不联网，演示一遍工具调用
    python main.py "读一下 requirements.txt 告诉我依赖"   # 用真模型
    python main.py                       # 交互模式
"""
from __future__ import annotations

import argparse


def parse_args():
    p = argparse.ArgumentParser(description="桌面智能体（命令行调试入口）")
    p.add_argument("task", nargs="?", help="要说的话；不给则进入交互模式")
    p.add_argument("--mock", action="store_true", help="用假模型，不发请求不花钱")
    p.add_argument("--demo", action="store_true", help="用假模型演示一次完整的工具调用")
    p.add_argument("--max-turns", type=int, default=None, help="循环上限")
    return p.parse_args()


def make_agent(mock: bool, max_turns: int | None, demo_script: list | None = None):
    from agent.loop import Agent

    def printer(event: dict) -> None:
        kind = event["type"]
        if kind == "tool_start":
            print(f"  [工具] {event['name']}({event['arguments']})")
        elif kind == "tool_end":
            flag = "成功" if event["ok"] else "失败"
            preview = event["content"].strip().splitlines()[:3]
            print(f"  [{flag} {event['elapsed']:.2f}s] " + " / ".join(preview))
        elif kind == "error":
            print(f"  [异常] {event['message']}")

    return Agent(mock=mock, mock_script=demo_script, max_turns=max_turns, on_event=printer)


DEMO_SCRIPT = [
    {"tool": "read", "args": {"path": "requirements.txt"}, "content": "我先看一下依赖文件。"},
    {"tool": "read", "args": {"path": "不存在的文件.py"}},  # 故意制造一次失败
    {"tool": "write", "args": {"path": "hello.txt", "content": "第一行\n第二行\n"}, "content": "写个文件试试。"},
    {"tool": "edit", "args": {"path": "hello.txt", "old_string": "第二行", "new_string": "第二行（改过）"}},
    {"tool": "bash", "args": {"command": "type hello.txt"}},
    "演示结束：读成功、读失败、写入、编辑、执行命令，四种工具全部走通。",
]


def main() -> None:
    args = parse_args()
    mock = args.mock or args.demo
    agent = make_agent(mock, args.max_turns, DEMO_SCRIPT if args.demo else None)

    if args.demo:
        result = agent.run("[demo] 演示一遍工具调用")
        print(f"\n最终回答：{result.answer}")
        if result.error:
            print(f"结束原因：{result.error}")
        return

    if args.task:
        result = agent.run(args.task)
        print(f"\n{result.answer}" if not result.error else f"\n[失败] {result.error}")
        return

    print("交互模式（输入 q 退出）")
    while True:
        try:
            user = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            break
        if user.lower() in {"q", "quit", "exit"}:
            break
        if not user:
            continue

        result = agent.run(user)
        print(f"\n助手 > {result.answer}" if not result.error else f"\n[失败] {result.error}")


if __name__ == "__main__":
    main()
