"""
agent.routines —— 定时触发（增量 C）

`routines.yaml` 长这样（故意只支持一个键值对，够用就行）：

    - name: nightly-test
      every: 3600
      skill: run-tests

为什么不用真 yaml 库：我们的格式只有三个字段，手写 20 行解析器比引依赖便宜，
也避免打包时多一个 C 扩展。

**幂等**是这个模块唯一的技术点：
    同一个时间槽（slot）只允许触发一次。定时线程被人为重启、或者两次 tick
    挤在一起时，宁可少跑一次，也不要重复跑一次——"跑两遍"在真实项目里
    可能意味着发两封邮件、改两次数据。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROUTINES_FILE = "routines.yaml"


@dataclass
class Routine:
    name: str
    every: int
    skill: str


def parse(text: str) -> list[Routine]:
    """极简解析：按 `- name:` 切块，块内 `key: value`。"""
    routines: list[Routine] = []
    current: dict[str, str] = {}

    def flush() -> None:
        if not current.get("name"):
            return
        try:
            every = int(current.get("every", "60"))
        except ValueError:
            every = 60
        routines.append(
            Routine(
                name=current["name"],
                every=max(1, every),
                skill=current.get("skill", ""),
            )
        )

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            flush()
            current = {}
            line = line[2:].strip()
        if ":" in line:
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip().strip('"').strip("'")
    flush()
    return routines


def load(workspace: Path) -> list[Routine]:
    path = Path(workspace) / ROUTINES_FILE
    if not path.is_file():
        return []
    try:
        return parse(path.read_text(encoding="utf-8"))
    except OSError:
        return []


@dataclass
class RoutineRunner:
    """在后台线程里按间隔触发。callback(routine) 由调用方决定做什么。"""

    routines: list[Routine]
    callback: Callable[[Routine], None]
    tick: float = 1.0
    fired: list[dict] = field(default_factory=list)
    _seen_slots: set[str] = field(default_factory=set)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="routines")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(self.tick)

    def poll(self, now: float | None = None) -> int:
        """检查一次，返回本次真正触发的条数。幂等靠 slot 去重。"""
        now = time.time() if now is None else now
        fired_now = 0
        for routine in self.routines:
            slot = f"{routine.name}:{int(now // routine.every)}"
            if slot in self._seen_slots:
                continue
            self._seen_slots.add(slot)
            record = {"name": routine.name, "skill": routine.skill, "at": round(now, 3), "slot": slot}
            self.fired.append(record)
            fired_now += 1
            try:
                self.callback(routine)
            except Exception as e:  # noqa: BLE001 - 定时任务失败不能拖垮线程
                record["error"] = f"{type(e).__name__}: {e}"
        return fired_now

    @property
    def triggers_fired(self) -> int:
        return len(self.fired)

    @property
    def idempotent(self) -> bool:
        """同一 slot 是否只出现过一次——重复即说明幂等破了。"""
        slots = [r["slot"] for r in self.fired]
        return len(slots) == len(set(slots))
