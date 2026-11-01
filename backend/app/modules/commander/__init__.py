"""指挥官 Agent 模块：计划 → 选兵 → 并行执行 → 汇总。

对外的稳定契约见 ``docs/upgrade-commander-agent.md`` §5 与本节 service 的 docstring。
对话链路由主 Agent 集成，本模块不修改 ``chat/`` 下任何文件。
"""
