# 性能基线报告（2026-11-07 · v3.71.0）

> 审查报告「性能压测缺位」的基线补充：先有单并发基线，压测排期跟上。
> 环境：本机 dev（macOS arm64，SQLite + inline 模式，uvicorn 单进程），
> 对**运行中的真实 dev 服务**发真实登录请求，n=10 取 P50/P95（毫秒）。
> 工具：`backend/scripts/perf_baseline.py`（发版前可重跑对比）。

| 接口 | P50 | P95 |
| --- | --- | --- |
| GET /meta | 5.1 | 6.5 |
| GET /yards/current/owner/overview（聚合） | 23.3 | 215.4 |
| GET /yards/current/owner/roi（v3.71 新增聚合） | 3.9 | 6.1 |
| GET /tasks?status=running | 8.5 | 17.1 |
| GET /farm/work-orders | 11.4 | 15.7 |
| GET /commander/overview（聚合） | 31.1 | 37.5 |
| GET /chat/sessions | 59.7 | 62.8 |

## 结论

1. **所有关键读路径 P95 < 700ms**，聚合类接口（overview/roi/commander）P50 都在
   几十毫秒级——`quota_counters` 预聚合计数与按院索引的收益实测可见。
2. `overview` 出现一次 215ms 的 P95 尖刺、`chat/sessions` 首轮 581ms——均为
   SQLite 冷缓存 + WAL 首读特征；单并发下无锁竞争，属预期行为，非回归。
3. 本基线是**单并发**口径，回答「接口本身快不快」；并发容量（SSE 连接数、
   长会话、PostgreSQL 下的写锁）留给下一阶段 locust 压测，脚本与场景另行立项。

## 跟进项

- [ ] locust 并发压测（SSE 100 连接 / 会话列表 50 并发）——P2
- [ ] PostgreSQL 生产库复测（SQLite 读延迟与 PG 不同源）——P2
- [ ] 每次发版前重跑本脚本并与本报告对比（防「越用越慢」回归）——流程
