"""技能市场（B1）：内置农业 Agent 种子包与小院自助装载。

设计约束（ADR-0005）：种子包全部为只读检索型低风险配置，装载走
declarative 的既有写入口（create_agent + publish_agent），不造第二套。
"""
