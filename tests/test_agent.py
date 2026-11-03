"""
智能体层自测：失败重试 + 上下文压缩（不需要 Key、不需要网络）

跑法：
    python tests/test_agent.py

为什么单独测这两件事：
    重试和压缩都属于"平时看不见、出事才救命"的机制。
    如果不写测试，你永远不知道它到底有没有生效——
    因为线上表现是"看起来也还行"，但真到演示那一刻就翻车。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import config
from agent.llm import DeepSeekLLM
from agent.loop import Agent

passed = 0
failed = 0


def check(title: str, condition: bool, detail: object = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {title}")
    else:
        failed += 1
        print(f"  FAIL  {title} {detail}")


class FakeResp:
    def __init__(self, status: int, body: str = "", payload: dict | None = None) -> None:
        self.status_code = status
        self.text = body
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def patch_post(sequence: list):
    """把 requests.post 换成一个按剧本返回的假函数，并记录调用次数。"""
    import requests

    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        idx = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        item = sequence[idx]
        if isinstance(item, Exception):
            raise item
        return item

    requests.post = fake_post
    return calls


def test_retry() -> None:
    import requests

    print("\n[1] 失败重试")

    # 场景一：网络抖动两次，第三次成功
    llm = DeepSeekLLM(api_key="sk-test")
    ok_payload = {"choices": [{"message": {"content": "重试成功了"}}], "usage": {}}
    calls = patch_post([
        requests.exceptions.ConnectionError("连接被重置"),
        requests.exceptions.Timeout("读超时"),
        FakeResp(200, payload=ok_payload),
    ])
    reply = llm.chat([{"role": "user", "content": "hi"}])
    check("两次网络异常后仍拿到回答", reply.content == "重试成功了", reply.content)
    check("总共请求了 3 次", calls["n"] == 3, calls["n"])

    # 场景二：Key 错误（401）不该重试，白等 3 次只会让演示更慢
    llm = DeepSeekLLM(api_key="sk-test")
    calls = patch_post([FakeResp(401, body='{"error":"invalid api key"}')])
    try:
        llm.chat([{"role": "user", "content": "hi"}])
        check("401 直接失败", False, "居然没抛异常")
    except RuntimeError as e:
        check("401 直接失败且提示清楚", "401" in str(e))
    check("401 只请求 1 次（不浪费时间）", calls["n"] == 1, calls["n"])

    # 场景三：服务端一直 500，重试次数用完要给出人话
    llm = DeepSeekLLM(api_key="sk-test")
    calls = patch_post([FakeResp(500, body="server error")])
    try:
        llm.chat([{"role": "user", "content": "hi"}])
        check("5xx 重试耗尽后报错", False, "居然没抛异常")
    except RuntimeError as e:
        check("5xx 重试耗尽后报错", "已重试 3 次" in str(e), str(e)[:80])
    check("5xx 请求了 3 次", calls["n"] == 3, calls["n"])


def test_compress() -> None:
    print("\n[2] 上下文压缩")

    events: list[dict] = []
    agent = Agent(mock=True, mock_script=["ok"], on_event=events.append)

    # 短历史不该被压缩（压缩本身也有代价，别没事找事）
    agent.messages.append({"role": "user", "content": "短对话"})
    check("短历史不压缩", agent.maybe_compress() is False)

    # 造一段很长的历史：模拟读了很多文件、跑了很久命令
    old_budget = config.context_budget
    old_keep = config.keep_recent
    config.context_budget, config.keep_recent = 800, 4
    try:
        agent.messages = [{"role": "system", "content": "你是助手"}]
        for i in range(12):
            agent.messages.append({"role": "user", "content": f"第 {i} 轮提问" + "废话" * 50})
            agent.messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": f"c{i}", "type": "function",
                                "function": {"name": "read", "arguments": '{"path":"a.py"}'}}],
            })
            agent.messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "文件内容" * 80})
        before = len(agent.messages)

        compressed = agent.maybe_compress()
        check("超预算时触发压缩", compressed is True)
        check("消息条数明显减少", len(agent.messages) < before, f"{before} → {len(agent.messages)}")
        check("system 提示词保住了", agent.messages[0]["content"] == "你是助手")
        check("第 2 条是摘要而不是原始历史", "压缩摘要" in agent.messages[1]["content"])
        check("摘要里带上了关键信息", "read" in agent.messages[1]["content"])
        check("最近几条保留下来", any("废话" in str(m.get("content")) for m in agent.messages[2:]))

        # 压缩的上限由"保留的最近几条 + 摘要"决定，不可能比这更小
        tail_chars = sum(
            len(str(m.get("content") or "")) + sum(
                len(str(c.get("function", {}))) for c in m.get("tool_calls") or []
            )
            for m in agent.messages[2:]
        )
        check(
            "压缩后只剩摘要 + 最近几条",
            agent.history_chars() <= tail_chars + config.context_budget,
            f"{agent.history_chars()} vs {tail_chars}",
        )

        # 最容易踩的坑：截断点落在 assistant/tool 之间，接口会直接 400
        check("保留段不以孤立的 tool 消息开头", agent.messages[2].get("role") != "tool",
              agent.messages[2].get("role"))

        notices = [e for e in events if e["type"] == "notice"]
        check("界面收到压缩提示", bool(notices) and "压缩" in notices[0]["message"], notices[:1])
    finally:
        config.context_budget, config.keep_recent = old_budget, old_keep


def main() -> None:
    test_retry()
    test_compress()
    print(f"\n结果：{passed} 通过，{failed} 失败")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
