"""
会话持久化自测：存了能读回、坏了不报错、悬空的工具调用要清掉

跑法：
    python tests/test_session.py

为什么这块特别值得测：
    它是"唯一会读写用户磁盘状态"的功能。
    写坏了不只是一个功能失效，而是下次启动直接崩溃——
    用户看到的是"程序打不开了"，比"历史没保存"严重十倍。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import session
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


def test_roundtrip(tmp: Path) -> None:
    print("\n[1] 存了能读回")
    path = tmp / "s1.json"
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "读一下 main.py"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read", "arguments": '{"path":"main.py"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "OK\n...文件内容..."},
        {"role": "assistant", "content": "main.py 是命令行入口。"},
    ]
    check("保存成功", session.save(messages, path) is True)
    check("文件真的落地了", path.exists())
    check("临时文件没残留", not path.with_suffix(".json.tmp").exists())

    loaded = session.load(path)
    check("能读回", isinstance(loaded, list) and len(loaded) == len(messages), len(loaded or []))
    check("中文没被转义成 \\u 乱码", "命令行入口" in path.read_text(encoding="utf-8"))
    check("工具配对完好", loaded[-2]["role"] == "tool")


def test_robustness(tmp: Path) -> None:
    print("\n[2] 文件坏了也不崩")
    bad = tmp / "bad.json"
    bad.write_text("{ 这不是 json", encoding="utf-8")
    check("坏 JSON 返回 None 而不是抛异常", session.load(bad) is None)

    wrong = tmp / "wrong.json"
    wrong.write_text(json.dumps({"messages": "应该是列表"}), encoding="utf-8")
    check("结构不对也返回 None", session.load(wrong) is None)

    check("文件不存在返回 None", session.load(tmp / "nope.json") is None)

    # 悬空工具调用：assistant 说有 tool_calls，但结果没来得及写进去
    dangling = [
        {"role": "user", "content": "跑个命令"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "x1", "type": "function",
                         "function": {"name": "bash", "arguments": "{}"}}]},
    ]
    cleaned = session.sanitize(dangling)
    check("悬空的工具调用被丢掉", all(m.get("role") != "assistant" or not m.get("tool_calls") for m in cleaned), cleaned)
    check("用户消息保留", any(m["role"] == "user" for m in cleaned))

    mixed = [
        {"role": "用户", "content": "非法 role"},
        {"role": "user", "content": "合法"},
        "字符串不是字典",
    ]
    check("非法条目被过滤", len(session.sanitize(mixed)) == 1, session.sanitize(mixed))


def test_agent_persist(tmp: Path) -> None:
    print("\n[3] Agent 自动存取")
    path = tmp / "agent.json"
    agent = Agent(mock=True, mock_script=["第一句回答", "第二句回答"], session_path=path)
    check("第一次启动没有历史可恢复", agent.restore() == 0)

    agent.run("第一个问题")
    agent.run("第二个问题")
    check("跑完自动落盘", path.exists())

    agent2 = Agent(mock=True, session_path=path)
    body = [m for m in agent2.messages if m["role"] != "system"]
    check("新实例恢复出历史", len(body) >= 4, len(body))
    check("恢复的是真实对话", any(m.get("content") == "第一个问题" for m in body))
    check("system 用当前提示词（且只有一条）",
          sum(1 for m in agent2.messages if m["role"] == "system") == 1)

    visible = agent2.visible_history()
    check("回放列表只有人话", all(v["role"] in {"user", "assistant"} for v in visible), visible)
    check("回放里没有空内容", all(v["content"].strip() for v in visible))

    agent2.reset()
    check("新建会话后文件被清掉", not path.exists())
    check("新建会话后内存也空了",
          [m for m in agent2.messages if m["role"] != "system"] == [])


def test_persist_off(tmp: Path) -> None:
    print("\n[4] 关掉持久化时不写盘")
    path = tmp / "off.json"
    agent = Agent(mock=True, mock_script=["hi"], session_path=path, persist=False)
    agent.run("随便问一句")
    check("persist=False 不创建文件", not path.exists())


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_roundtrip(tmp)
        test_robustness(tmp)
        test_agent_persist(tmp)
        test_persist_off(tmp)
    print(f"\n结果：{passed} 通过，{failed} 失败")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
