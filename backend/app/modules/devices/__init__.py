"""智慧农业设备接入模块（A 线：devices 统一模型 + webhook 接入 + 自定义连接器 + 告警）。

数据流：设备/厂商云 → ingest（推送）或 connector 轮询（拉取）→ readings 时序表
→ 阈值规则比对 → device_alerts 告警。只读+建议，不反控设备（用户拍板红线）。
"""
