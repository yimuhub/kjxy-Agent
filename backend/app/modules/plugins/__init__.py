"""插件机制：发现、注册表、启用/禁用（P1 第四期）。

规范 V3.0 §9.4/§9.5：
- 插件类型：workflow / knowledge_pack / domain_pack（首版）
- manifest 最少字段：plugin_id / name / version / type / api_compatibility /
  dependencies / permissions / entry_point / config_schema / checksum
- 最小权限、停用/灰度/回滚、卸载不删业务数据（停用只改状态）
- 插件默认 scope 为全局（platform），tenant_id 保留、首版全空=全局可见
"""
