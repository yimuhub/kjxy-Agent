# Hello 演示插件

`plugin_id`: `demo.hello` ｜ 类型：`workflow` ｜ 版本：`1.0.0`

插件机制的最小演示插件，不含任何业务逻辑，仅用于验证：

1. `POST /api/v1/plugins/scan` 扫描 `plugins/` 目录后能发现它；
2. `GET /api/v1/plugins` 列表中可见，初始状态为 `disabled`；
3. `POST /api/v1/plugins/demo.hello/enable` / `disable` 能翻转状态；
4. `manifest.json` 的 checksum 与内容一致（扫描不告警）。

真实插件的 `manifest.json` 必填字段（V3.0 §9.4）：

`plugin_id` / `name` / `version` / `type` / `api_compatibility` /
`dependencies` / `permissions` / `entry_point` / `config_schema` / `checksum`

checksum 算法：对除 `checksum` 外的全部字段做规范 JSON 序列化
（`sort_keys=True`、`ensure_ascii=False`、`separators=(",", ":")`），取 sha256 十六进制。
