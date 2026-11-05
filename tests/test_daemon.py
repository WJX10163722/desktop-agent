"""
常驻能力自测（增量 C）：断点续跑 / 定时触发 / 完成通知

跑法：
    python tests/test_daemon.py

这一组最想证明的是"续跑不是把话说一遍"：
    任务被掐断在第 N 轮，重启后历史、轮次、现场都要对得上，
    否则 resume_from_step 就是个自欺欺人的数字。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import checkpoint, notify
from agent import routines as routines_mod
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


def test_checkpoint_io(tmp: Path) -> None:
    print("\n[1] checkpoint 读写")
    path = tmp / "checkpoint.json"
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "干活"}]
    check("保存成功", checkpoint.save(path, "干活", 3, msgs) is True)
    data = checkpoint.load(path)
    check("能读回 step", data is not None and data["step"] == 3, data and data["step"])
    check("能读回 user_input", data is not None and data["user_input"] == "干活")

    broken = tmp / "broken.json"
    broken.write_text("{坏掉的", encoding="utf-8")
    check("坏文件返回 None", checkpoint.load(broken) is None)

    checkpoint.clear(path)
    check("清除后文件消失", not path.exists())


def test_resume(tmp: Path) -> None:
    print("\n[2] 断点续跑")
    session_path = tmp / "last.json"
    script = [{"tool": "read", "args": {"path": "requirements.txt"}}] * 4

    crashed = Agent(mock=True, mock_script=script, session_path=session_path, max_turns=2)
    result = crashed.run("读四遍依赖")
    check("被 max_turns 掐断", result.stop_reason == "max_turns", result.stop_reason)

    ckpt = checkpoint.load(checkpoint.path_for(session_path))
    check("掐断后 checkpoint 还在", ckpt is not None)
    check("记录了做到第几步", ckpt is not None and ckpt["step"] == 2, ckpt and ckpt["step"])

    restarted = Agent(mock=True, session_path=session_path)
    step = restarted.resume()
    check("重启后能续跑", step == 2, step)
    check("现场消息恢复了", len(restarted.messages) >= 5, len(restarted.messages))
    check("恢复的历史可被接口接受（无悬空 tool）",
          all(m["role"] != "tool" or any(
              c.get("id") == m.get("tool_call_id")
              for prev in restarted.messages[:i] if prev["role"] == "assistant"
              for c in prev.get("tool_calls") or []
          ) for i, m in enumerate(restarted.messages) if m["role"] == "tool"))

    # 正常跑完就不该留下 checkpoint，否则下次启动会"续跑"一个已完成任务
    done_agent = Agent(mock=True, mock_script=["收工"], session_path=tmp / "done.json")
    done_agent.run("随便问")
    check("正常结束后 checkpoint 被清掉",
          not checkpoint.path_for(tmp / "done.json").exists())


def test_routines() -> None:
    print("\n[3] 定时触发")
    text = """
# 每晚跑一次测试
- name: nightly-test
  every: 3600
  skill: run-tests
- name: brief
  every: 60
  skill: project-brief
"""
    items = routines_mod.parse(text)
    check("解析出两条", len(items) == 2, len(items))
    check("字段正确", items[0].name == "nightly-test" and items[0].every == 3600 and items[0].skill == "run-tests")

    fired: list[str] = []
    runner = routines_mod.RoutineRunner([routines_mod.Routine("t", 10, "")], lambda r: fired.append(r.name))
    check("第一次 poll 触发", runner.poll(now=100.0) == 1)
    check("同一时间槽不重复触发（幂等）", runner.poll(now=100.5) == 0)
    check("下一个槽再触发", runner.poll(now=111.0) == 1)
    check("触发次数正确", runner.triggers_fired == 2, runner.triggers_fired)
    check("幂等标记为真", runner.idempotent is True)

    check("没有 routines.yaml 时返回空", routines_mod.load(Path(tempfile.gettempdir())) == [])


def test_notify() -> None:
    print("\n[4] 完成通知")
    result = notify.notify("自测", "这是一条测试通知", timeout=15)
    check("通知调用不抛异常且返回布尔", isinstance(result, bool), result)
    if not result:
        print("       （没弹出来不一定是 bug：无桌面会话/权限受限时会返回 False）")


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_checkpoint_io(tmp)
        test_resume(tmp)
    test_routines()
    test_notify()
    print(f"\n结果：{passed} 通过，{failed} 失败")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
