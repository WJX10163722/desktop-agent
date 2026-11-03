"""
工具层自测（不联网、不花钱、不需要 Key）

跑法：
    python tests/test_tools.py

为什么要有这个文件：
    答辩时"你怎么证明工具是可靠的"——不是嘴上说，跑一遍给他看。
    每加一个工具就往这里补几条断言，改坏了立刻知道。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import config  # noqa: E402
from agent.tools import build_default_registry  # noqa: E402

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


def main() -> None:
    reg = build_default_registry()
    tmp = config.workspace / "_selftest"
    tmp.mkdir(exist_ok=True)

    print("\n[1] 注册表基础")
    check("read / write 已注册", {"read", "write"} <= set(reg.names), reg.names)
    check("schemas 是 OpenAI function 格式", reg.schemas()[0]["type"] == "function")

    print("\n[2] 注册表兜底（模型乱调时不许崩）")
    r = reg.call("不存在的工具", "{}")
    check("未知工具返回失败且列出可用工具", not r.ok and "read" in r.content)
    r = reg.call("read", "{这不是json}")
    check("非法 JSON 返回失败", not r.ok and "JSON" in r.content)
    r = reg.call("read", "{}")
    check("缺必填参数返回失败", not r.ok and "path" in r.content)
    r = reg.call("read", '{"path": 123}')
    check("参数类型错误被纠正/兜住", isinstance(r, object))

    print("\n[3] read 的四种现实情况")
    r = reg.call("read", '{"path": "requirements.txt"}')
    check("读普通文件成功并带行号", r.ok and "1|" in r.content, r.content[:80])
    r = reg.call("read", '{"path": "没有这个文件.xyz"}')
    check("文件不存在给出人话提示", not r.ok and "bash" in r.content)
    r = reg.call("read", '{"path": "."}')
    check("读目录被拒绝", not r.ok and "目录" in r.content)

    big = tmp / "big.txt"
    big.write_text("\n".join(f"line {i}" for i in range(1, 301)), encoding="utf-8")
    r = reg.call("read", f'{{"path": "_selftest/big.txt", "offset": 251, "limit": 100}}')
    check("分页读取正确", r.ok and "line 251" in r.content and "line 300" in r.content)
    check("分页提示还有多少行", "offset=" not in r.content)

    r = reg.call("read", '{"path": "_selftest/big.txt", "offset": 9999}')
    check("offset 越界给出总行数", not r.ok and "300" in r.content)

    print("\n[4] write 的三种情况")
    nested = "_selftest/deep/nested/new.txt"
    r = reg.call("write", f'{{"path": "{nested}", "content": "hello"}}')
    check("父目录自动创建", r.ok and (config.workspace / nested).exists())
    r = reg.call("write", '{"path": "_selftest/empty.txt", "content": ""}')
    check("写空内容被允许但明确提示", r.ok and "为空" in r.content)

    print("\n[5] 沙箱：不许跑出工作目录")
    r = reg.call("read", '{"path": "../../../../Windows/win.ini"}')
    check("越界路径被拒绝", not r.ok and "工作目录之外" in r.content)
    r = reg.call("write", '{"path": "../escape.txt", "content": "x"}')
    check("越界写被拒绝", not r.ok)

    # 收尾：删掉测试产生的临时文件，别把仓库搞脏
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n结果：{passed} 通过，{failed} 失败")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
