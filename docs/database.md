# 数据库说明

- 主库：PostgreSQL 17（`pgvector/pgvector:pg17` 镜像），向量列使用 pgvector 类型。
- 主键统一 UUID 字符串；时间统一 UTC 带时区；表名复数 snake_case。
- 所有变更经 Alembic migration（`backend/migrations/`），禁止手动改库不提交脚本。

## 核心表

| 表 | 用途 | 关键字段 |
|---|---|---|
| yards | 小院 | name、status、province、city、scene_type、config_json、owner_user_id（负责人） |
| users | 用户 | username（唯一）、email、phone（唯一）、password_hash、display_name、status、last_login_at、must_change_password、token_version |
| yard_memberships | 用户-小院关系 | yard_id、user_id、role、status；唯一 (yard_id,user_id) |
| tasks | 任务主记录 | yard_id、created_by、task_type、status、input_json、result_json、error_code、error_message、idempotency_key（小院内唯一）、priority、started_at、finished_at |
| task_events | 任务进度事件 | yard_id、task_id、event_type、message、progress、payload_json |
| agent_definitions | Agent 定义 | code、name、version、enabled、config_schema_json |
| agent_runs | Agent 运行记录 | yard_id、task_id、agent_code、status、input_summary、output_summary、duration_ms |
| llm_calls | 模型调用与成本 | yard_id、task_id、provider、model、input_tokens、output_tokens、estimated_cost、status、error_category、latency_ms、request_id、prompt_sha（不存原文） |
| llm_traces | 调用追踪（v3.0 增加归属） | yard_id、user_id、session_id、trace_id、steps_json；供驾驶舱按人汇总 |
| knowledge_documents | 原始知识文档 | yard_id、title、storage_key（唯一）、mime_type、source、status、checksum、uploaded_by、error_message、page_count、deleted_at |
| knowledge_chunks | 切块 | yard_id、document_id、chunk_index、content、page_number、title_path、trust_level、visibility、metadata_json、embedding（pgvector） |
| feedback | 反馈 | yard_id、task_id、actor_id、rating、feedback_text、corrected_answer、status |
| knowledge_candidates | 待审核知识 | yard_id、source_feedback_id、title、content、source_type、status、reviewed_by、reviewed_at、review_note |
| skills | Skill 定义/版本 | yard_id（scope）、skill_code、version、definition_json、status、created_from、approved_by；唯一 (yard_id,skill_code,version) |
| evolution_skills | Skill 定义/版本（v3.2 归属） | yard_id（可空，平台级）、skill_code、version、definition_json、status、visibility（private/yard/platform）、owner_user_id、origin、reviewed_by、reviewed_at、review_note；唯一 (yard_id,skill_code,version) |
| tool_definitions | 工具定义 | code（唯一）、version、input_schema_json、output_schema_json、required_permissions_json、risk_level、side_effect、timeout_seconds、enabled |
| audit_logs | 审计 | yard_id（可空）、actor_id、action、resource_type、resource_id、result、request_id、metadata |
| policies | 政策条目（P1） | scope、province、department、policy_name、source_url、published_at、apply_start_at、apply_end_at、status、verified_at、content_hash |
| yard_settings | 小院设置域 | yard_id、key（registration/security/search/skill_lab）、value_json、updated_by；唯一 (yard_id,key) |
| yard_roles | 角色与权限覆盖 | yard_id、code、name、permissions_json、is_system；唯一 (yard_id,code) |
| yard_member_grants | 负责人逐人敏感授权 | yard_id、user_id、grants_json、updated_by；唯一 (yard_id,user_id) |
| yard_join_requests | 自助注册/加入申请 | yard_id、user_id、kind、status、reviewed_by、reject_reason |
| market_items | 市场条目缓存 | item_key（唯一）、item_type、source、code、definition_json、manifest_json、metadata_json、verified、status |
| supplier_models | 供应商下的多模型（v3.36） | supplier_id、model_key（供应商内唯一）、upstream_model_id、display_name、modality、protocol、input/output_modalities、capabilities、context_window、max_output_tokens、endpoint_path、pricing、default_params、limits_json、status、source、external_id、last_test_at、test_ok、latency_ms、last_error |
| relay_channels | 官方/私有 API 供应商（v3.36） | yard_id（空=平台）、name、kind（official/private）、base_url、api_key_enc、public_key、webhook_secret_enc、catalog_path、enabled、status、last_sync_at、last_error、metadata_json |
| official_plans | 官方通道套餐 | model_key、name、billing_unit、price、currency、trial_tokens、trial_days、active、metadata_json |
| official_entitlements | 官方权益（试用/购买） | yard_id、user_id、plan_id、model_key、source（trial/order）、status、quota_total、quota_used、starts_at、expires_at、external_ref |
| official_orders | 官方购买订单 | yard_id、user_id、plan_id、amount、currency、status、external_order_id、payment_url、idempotency_key、paid_at |
| official_webhook_events | 支付回调幂等 | event_id、event_type、payload_digest、received_at、processed_at |
| library_sources | 官方技能/插件库源（v3.36） | yard_id（空=平台官方源）、name、kind（official/private）、base_url、channel、public_key_b64、key_id、enabled、etag、sequence、status、last_checked_at、last_sync_at、last_error、metadata_json |
| library_items | 库条目清单镜像 | source_id、item_key、item_type、code、name、version、min/max_app_version、description、category、risk_level、license、author、sha256、size_bytes、download_url、manifest_json、status、sequence、published_at；唯一 (source_id,item_key,version) |
| library_installs | 本机安装台账 | yard_id、source_id、item_key、item_type、installed_version、previous_version、package_sha256、skill_id、plugin_id、auto_update、pinned、installed_at；唯一 (yard_id,item_key) |
| library_sync_runs | 同步运行记录 | source_id、yard_id、status（ok/error/skipped）、started_at、finished_at、added/updated/removed、error |
| org_units | 组织单元树（v3.57） | yard_id、parent_id、code、name、kind（company/coop/department/team）、description、sort_order、status（active/archived）、source（manual/knowledge/generated）；唯一 (yard_id,code) |
| org_positions | 岗位与 Agent 绑定（v3.57） | yard_id、org_unit_id、title、responsibilities、headcount、agent_id（软关联）、sort_order、status |
| org_proposals | 知识库推导的架构建议（v3.58） | yard_id、status（generating/ready/applied/discarded/failed）、task_id、instruction、title、summary、payload_json（units/positions/agent_suggestions）、evidence_json、model、prompt_version、tokens_in/out、error、applied_result_json、applied_at、created_by；索引 (yard_id,status) |
| plugin_consents | 插件权限授权台账（v3.59） | yard_id、item_key、plugin_id、plugin_version、scope_hash（权限集合指纹）、permissions_json、risk_level、ack_version、accepted_by/name/at、revoked_at/by；唯一 (yard_id,item_key,scope_hash) |

## 索引与完整性

- 所有 `yard_id` 建索引；`yard_memberships` 联合唯一；`tasks.idempotency_key` 小院内唯一；`knowledge_chunks.document_id` 外键。
- `llm_calls`、`audit_logs` 不存密钥与完整敏感提示词。
- 大表归档策略在生产部署前单独确认。

## 迁移

```bash
make migrate                 # 升级到 head
docker compose exec api alembic revision --autogenerate -m "描述"   # 新建迁移（需人工审查）
```

规则：每次变更新增 migration；不修改已共享的旧 migration；发布前在副本库演练。
