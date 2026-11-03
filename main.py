"""
main.py —— 命令行入口

现在的形态（D1）：命令行交互，用来验证后端逻辑。
最终的形态（D4 之后）：这个 CLI 会保留下来当调试口，
   真正的"产品"是 server + 桌面窗口。
"""
from __future__ import annotations

import argparse


def parse_args():
    p = argparse.ArgumentParser(description="桌面智能体（命令行调试入口）")
    p.add_argument("task", nargs="?", help="要说的话；不给则进入交互模式")
    p.add_argument("--mock", action="store_true", help="用假模型，不发请求不花钱")
    p.add_argument("--max-turns", type=int, default=None, help="循环上限")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    from agent.loop import Agent

    agent = Agent(mock=args.mock, max_turns=args.max_turns)

    if args.task:
        result = agent.run(args.task)
        if result.error:
            print(f"[失败] {result.error}")
        else:
            print(result.answer)
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
        print(f"助手 > {result.answer}" if not result.error else f"[失败] {result.error}")


if __name__ == "__main__":
    main()
