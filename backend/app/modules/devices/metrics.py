"""内置指标字典（统一口径）。

所有接入通道（webhook 推送 / 连接器拉取）的字段最终归一到本字典的 code；
字典外的字段一律进 ``other`` 且原始报文保留在 raw_json，保证"先接进来再谈口径"。

字段映射选型（不加 jsonpath 依赖）：连接器 ``field_map`` 的 key 为厂商字段名，
value 为 ``{"metric": <code 或 "other">, "unit": 可选覆盖单位}``；厂商字段本身
支持点分路径与 ``[*]``（由 connector.extract_path 自实现，见该模块）。
"""

from dataclasses import dataclass

#: 字典外字段的兜底 metric code
OTHER_METRIC = "other"


@dataclass(frozen=True)
class MetricInfo:
    code: str
    name: str  # 中文名（前端渲染）
    unit: str  # 默认单位（空串=无单位/枚举值）


def _m(code: str, name: str, unit: str = "") -> MetricInfo:
    return MetricInfo(code=code, name=name, unit=unit)


#: 指标字典：code -> MetricInfo。新增指标只加条目，不改消费方。
METRICS: dict[str, MetricInfo] = {
    m.code: m
    for m in [
        _m("temp", "气温", "℃"),
        _m("humidity", "空气湿度", "%RH"),
        _m("rain", "降雨量", "mm"),
        _m("wind_speed", "风速", "m/s"),
        _m("wind_dir", "风向", "°"),
        _m("light", "光照强度", "lux"),
        _m("co2", "二氧化碳浓度", "ppm"),
        _m("soil_moisture_10", "土壤水分(10cm)", "%"),
        _m("soil_moisture_20", "土壤水分(20cm)", "%"),
        _m("soil_moisture_40", "土壤水分(40cm)", "%"),
        _m("soil_moisture_60", "土壤水分(60cm)", "%"),
        _m("soil_temp", "土壤温度", "℃"),
        _m("soil_ec", "土壤电导率", "μS/cm"),
        _m("soil_ph", "土壤pH", ""),
        _m("water_level", "水位", "cm"),
        _m("pest_count", "虫情计数", "只"),
        _m("battery", "设备电量", "%"),
        _m(OTHER_METRIC, "未归类指标", ""),
    ]
}

#: 设备类型枚举（接入时选定，仅用于前端分类展示）
DEVICE_TYPES = ("weather", "soil", "pest", "greenhouse", "other")
#: 接入方式：webhook=设备侧带 token 推送；connector=平台按周期拉取厂商接口
SOURCE_TYPES = ("webhook", "connector")


def metric_info(code: str) -> MetricInfo | None:
    return METRICS.get(code)


def normalize_code(raw: str) -> str:
    """把上报字段名尽力归一到字典 code：精确命中 > 小写去空格命中 > other。

    注意：归一仅做大小写/空白宽容，不做模糊猜测（避免把 vendor 私有字段
    错映射到标准指标——错映射比进 other 更危险，后者原始数据仍在 raw）。
    """
    key = (raw or "").strip().lower()
    if key in METRICS:
        return key
    return OTHER_METRIC
