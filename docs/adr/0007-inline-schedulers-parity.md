# ADR-0007：inline（桌面单机）模式的后台巡检与 Celery beat 对齐（v3.71）

日期：2026-11-07 · 状态：已接受

## 背景

桌面客户端（Tauri sidecar）与本地部署以 `TASKS_INLINE_MODE` 运行：API 进程内
起 daemon 线程替代 Celery worker/beat。审查发现巡检清单不对齐——`devices`/
`self_improve`/`memory`/`commander` 评测/学习/迭代都有 inline 调度，但
**工单逾期升级只有 Celery beat（每 6 小时）**，桌面端用户的逾期工单永远不会
自动升级（审查报告 P1 项）。

## 决策

1. 补齐 `app/modules/farm/worker_tasks.py`：与 devices 相同的 daemon 模式
   （幂等启动、Event 停机、单轮异常全兜住），每 6 小时跑一次
   `workorder_service.escalate_overdue`，与 beat 口径一致。
2. 规则固化：**凡注册进 `workers/celery_app.py::beat_schedule` 的周期任务，
   必须同步检查 inline 模式是否有等价线程**；新增 beat 任务时两处一起加，
   PR 描述里注明（参照 devices.poll_connectors 的注释先例）。

## 后果

- 桌面端逾期升级生效；测试用注入会话直测 `run_inline_escalate_once`
  （test_farm_worker_tasks.py）。
- 两套调度（beat / inline）长期并存是已知代价：桌面形态是本产品主交付形态，
  正确性优先于收敛调度器；若未来统一，以 APScheduler 或 Celery 单轨收口另立 ADR。
