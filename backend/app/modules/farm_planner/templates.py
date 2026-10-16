"""农事规划规则模板数据：作物生育期任务模板 + 通用模板。

从 service.py 拆分出来，方便同步。数据与工作区 service.py 的
TASK_TEMPLATES / GENERIC_TASKS 完全一致。
"""

from typing import Any

TASK_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "小麦": [
        {
            "stage": "播种期",
            "tasks": [
                {
                    "code": "sowing",
                    "name": "播种",
                    "offset_days": 0,
                    "window_days": 7,
                    "materials": [{"name": "小麦种子", "per_mu": 12.5, "unit": "公斤"}],
                    "dependencies": ["整地完成", "土壤墒情适宜"],
                    "risks": ["播种过晚影响冬前分蘖", "播量过大易倒伏"],
                },
                {
                    "code": "base_fertilizer",
                    "name": "施底肥",
                    "offset_days": 0,
                    "window_days": 7,
                    "materials": [{"name": "复合肥", "per_mu": 40.0, "unit": "公斤"}],
                    "dependencies": ["与播种同步或提前完成"],
                    "risks": ["底肥不足影响苗期长势"],
                },
            ],
        },
        {
            "stage": "分蘖期",
            "tasks": [
                {
                    "code": "seedling_check",
                    "name": "查苗补种",
                    "offset_days": 10,
                    "window_days": 7,
                    "materials": [],
                    "dependencies": ["出苗后田间查验"],
                    "risks": ["缺苗断垄影响基本苗数"],
                },
            ],
        },
        {
            "stage": "返青期",
            "tasks": [
                {
                    "code": "topdressing_regreen",
                    "name": "返青追肥",
                    "offset_days": 2,
                    "window_days": 7,
                    "materials": [{"name": "尿素", "per_mu": 10.0, "unit": "公斤"}],
                    "dependencies": ["土壤墒情适宜或具备灌溉条件"],
                    "risks": ["倒春寒可能影响肥效", "旺苗追肥易倒伏"],
                },
                {
                    "code": "irrigation_regreen",
                    "name": "返青灌溉",
                    "offset_days": 3,
                    "window_days": 7,
                    "materials": [],
                    "dependencies": ["灌溉水源可用", "天气预报无强降温"],
                    "risks": ["灌水过量遇倒春寒加重冻害"],
                },
            ],
        },
        {
            "stage": "拔节期",
            "tasks": [
                {
                    "code": "topdressing_jointing",
                    "name": "拔节追肥",
                    "offset_days": 2,
                    "window_days": 7,
                    "materials": [{"name": "尿素", "per_mu": 8.0, "unit": "公斤"}],
                    "dependencies": ["结合灌水进行"],
                    "risks": ["追肥偏晚影响成穗率"],
                },
                {
                    "code": "pest_control_jointing",
                    "name": "拔节期病虫防治",
                    "offset_days": 4,
                    "window_days": 7,
                    "materials": [{"name": "吡虫啉", "per_mu": 10.0, "unit": "克"}],
                    "dependencies": ["田间调查确认虫情", "无风或微风天气施药"],
                    "risks": ["蚜虫、白粉病暴发", "施药须遵守安全间隔期"],
                },
            ],
        },
        {
            "stage": "抽穗期",
            "tasks": [
                {
                    "code": "pest_control_heading",
                    "name": "抽穗期病害防治",
                    "offset_days": 2,
                    "window_days": 7,
                    "materials": [{"name": "戊唑醇", "per_mu": 30.0, "unit": "克"}],
                    "dependencies": ["扬花初期用药", "关注天气预报避开降雨"],
                    "risks": ["赤霉病流行年份须抢晴施药"],
                },
            ],
        },
        {
            "stage": "灌浆期",
            "tasks": [
                {
                    "code": "foliar_fertilizer",
                    "name": "叶面喷肥",
                    "offset_days": 2,
                    "window_days": 7,
                    "materials": [{"name": "磷酸二氢钾", "per_mu": 0.2, "unit": "公斤"}],
                    "dependencies": ["灌浆中后期、晴天下午施用"],
                    "risks": ["干热风影响灌浆"],
                },
            ],
        },
        {
            "stage": "成熟期",
            "tasks": [
                {
                    "code": "harvest",
                    "name": "收获",
                    "offset_days": 2,
                    "window_days": 10,
                    "materials": [],
                    "dependencies": ["籽粒含水量达标", "联合收割机调度到位", "连续晴好天气"],
                    "risks": ["收获过晚遇雨致穗发芽", "机收损失率须控制"],
                },
            ],
        },
    ],
    "玉米": [
        {
            "stage": "播种期",
            "tasks": [
                {
                    "code": "sowing",
                    "name": "播种",
                    "offset_days": 0,
                    "window_days": 7,
                    "materials": [{"name": "玉米种子", "per_mu": 2.0, "unit": "公斤"}],
                    "dependencies": ["5cm 地温稳定通过 10℃", "土壤墒情适宜"],
                    "risks": ["播种过早遇低温烂种"],
                },
            ],
        },
        {
            "stage": "苗期",
            "tasks": [
                {
                    "code": "thinning",
                    "name": "间苗定苗",
                    "offset_days": 8,
                    "window_days": 7,
                    "materials": [],
                    "dependencies": ["3-5 叶期完成"],
                    "risks": ["定苗过晚形成弱苗"],
                },
            ],
        },
        {
            "stage": "拔节期",
            "tasks": [
                {
                    "code": "topdressing_jointing",
                    "name": "拔节追肥",
                    "offset_days": 2,
                    "window_days": 7,
                    "materials": [{"name": "尿素", "per_mu": 15.0, "unit": "公斤"}],
                    "dependencies": ["结合中耕培土", "雨前或灌水前追施"],
                    "risks": ["追肥过量易倒伏"],
                },
            ],
        },
        {
            "stage": "抽穗期",
            "tasks": [
                {
                    "code": "irrigation_heading",
                    "name": "抽穗灌溉",
                    "offset_days": 2,
                    "window_days": 7,
                    "materials": [],
                    "dependencies": ["大喇叭口期至抽雄期需水关键期"],
                    "risks": ["花期干旱导致缺粒", "灌水过量遇大风倒伏"],
                },
            ],
        },
        {
            "stage": "成熟期",
            "tasks": [
                {
                    "code": "harvest",
                    "name": "收获",
                    "offset_days": 2,
                    "window_days": 10,
                    "materials": [],
                    "dependencies": ["籽粒乳线消失、含水量达标", "收获机械调度到位"],
                    "risks": ["收获过早影响产量", "秸秆还田须粉碎合格"],
                },
            ],
        },
    ],
    "水稻": [
        {
            "stage": "育秧期",
            "tasks": [
                {
                    "code": "nursery",
                    "name": "育秧",
                    "offset_days": 0,
                    "window_days": 10,
                    "materials": [{"name": "水稻种子", "per_mu": 3.0, "unit": "公斤"}],
                    "dependencies": ["秧田整理平整", "浸种催芽完成"],
                    "risks": ["低温烂秧", "秧苗徒长"],
                },
            ],
        },
        {
            "stage": "插秧期",
            "tasks": [
                {
                    "code": "transplanting",
                    "name": "插秧",
                    "offset_days": 10,
                    "window_days": 7,
                    "materials": [],
                    "dependencies": ["本田整平、水层适宜", "秧龄适宜"],
                    "risks": ["插秧过深返青慢"],
                },
            ],
        },
        {
            "stage": "分蘖期",
            "tasks": [
                {
                    "code": "topdressing_tillering",
                    "name": "分蘖追肥",
                    "offset_days": 5,
                    "window_days": 7,
                    "materials": [{"name": "尿素", "per_mu": 8.0, "unit": "公斤"}],
                    "dependencies": ["浅水层追施"],
                    "risks": ["追肥过量无效分蘖多"],
                },
                {
                    "code": "drainage",
                    "name": "晒田控蘖",
                    "offset_days": 12,
                    "window_days": 10,
                    "materials": [],
                    "dependencies": ["够苗后排水", "土壤承载力允许机械下田"],
                    "risks": ["晒田过度伤根"],
                },
            ],
        },
        {
            "stage": "抽穗期",
            "tasks": [
                {
                    "code": "pest_control_heading",
                    "name": "抽穗期病虫防治",
                    "offset_days": 2,
                    "window_days": 7,
                    "materials": [{"name": "三环唑", "per_mu": 30.0, "unit": "克"}],
                    "dependencies": ["破口期用药", "避开高温时段施药"],
                    "risks": ["稻瘟病、稻飞虱暴发", "须遵守安全间隔期"],
                },
            ],
        },
        {
            "stage": "成熟期",
            "tasks": [
                {
                    "code": "harvest",
                    "name": "收获",
                    "offset_days": 2,
                    "window_days": 10,
                    "materials": [],
                    "dependencies": ["黄熟期、田间可断水落干", "收割机调度到位"],
                    "risks": ["收获过晚落粒损失大"],
                },
            ],
        },
    ],
}

GENERIC_TASKS: list[dict[str, Any]] = [
    {
        "code": "sowing",
        "name": "播种",
        "stage": "播种期",
        "offset_days": 0,
        "window_days": 7,
        "materials": [],
        "dependencies": ["整地完成", "土壤墒情适宜"],
        "risks": ["播期不当影响出苗"],
    },
    {
        "code": "fertilizing",
        "name": "施肥",
        "stage": "生长期",
        "offset_days": 10,
        "window_days": 7,
        "materials": [],
        "dependencies": ["按土壤测试结果确定肥料种类与用量"],
        "risks": ["过量施肥造成烧苗或面源污染"],
    },
    {
        "code": "irrigation",
        "name": "灌溉",
        "stage": "生长期",
        "offset_days": 14,
        "window_days": 7,
        "materials": [],
        "dependencies": ["灌溉水源可用", "土壤墒情实测偏低"],
        "risks": ["灌水过量引发病害"],
    },
    {
        "code": "pest_control",
        "name": "病虫害防治",
        "stage": "生长期",
        "offset_days": 20,
        "window_days": 7,
        "materials": [],
        "dependencies": ["田间调查确认发生情况", "按农药标签安全使用"],
        "risks": ["须遵守安全间隔期"],
    },
    {
        "code": "harvest",
        "name": "收获",
        "stage": "成熟期",
        "offset_days": 30,
        "window_days": 10,
        "materials": [],
        "dependencies": ["成熟度达标", "收获机械/人力调度到位"],
        "risks": ["收获不及时造成损失"],
    },
]
