"""app —— 验收命令包

作业要求的三条验收命令都从这里进：
    python -m app.selftest                          # v1.0 冒烟自检
    python -m app.inspect skills --out skill_report.json
    python -m app.inspect custom --out custom_report.json

为什么单独建 app/ 包：验收命令要"不靠界面独立跑通"，
所以它只依赖 agent/ 层，绝不 import server/ 和 ui/。
"""
