"""一句话解析的规则层：数字/面积/日期/名称/类型都可复核。"""

from datetime import date

from app.modules.quick_actions import parser


def test_chinese_and_arabic_numbers():
    assert parser._to_number("12") == 12
    assert parser._to_number("3.5") == 3.5
    assert parser._to_number("十二") == 12
    assert parser._to_number("二十") == 20
    assert parser._to_number("二十五") == 25
    assert parser._to_number("一百二十") == 120
    assert parser._to_number("") is None


def test_area_units_normalized_to_mu():
    assert parser._parse_area("12 亩") == 12
    assert parser._parse_area("一公顷") == 15
    assert parser._parse_area("667 平方米") == 1.0
    assert parser._parse_area("没有面积") is None
    assert parser._parse_area("0 亩") is None


def test_quantity_ignores_area_units():
    assert parser._parse_quantity("尿素 5 公斤") == (5, "公斤")
    assert parser._parse_quantity("灌水 2 吨") == (2, "吨")
    # 面积单位不是用量，不能被当成化肥/农药数量
    assert parser._parse_quantity("12 亩") == (None, "")


def test_relative_and_absolute_dates():
    today = date(2026, 10, 29)
    assert parser._parse_date("今天追肥", today) == today
    assert parser._parse_date("昨天打药", today) == date(2026, 10, 28)
    assert parser._parse_date("明天到期", today) == date(2026, 10, 30)
    assert parser._parse_date("2026-11-02 巡田", today) == date(2026, 11, 2)
    assert parser._parse_date("11月3日 巡田", today) == date(2026, 11, 3)


def test_plot_name_cut_at_crop_area_soil():
    rest = " 东一号田 水稻 12 亩，沙壤土"
    assert parser._cut_name(rest) == "东一号田"
    rest = "西三号田，8亩"
    assert parser._cut_name(rest) == "西三号田"


def test_record_kind_detection():
    assert parser._record_kind("今天追肥 尿素 5 公斤") == "fertilizing"
    assert parser._record_kind("给地块浇水") == "irrigation"
    assert parser._record_kind("打药防治蚜虫") == "pesticide"
    assert parser._record_kind("巡田看看长势") == "scouting"
    assert parser._record_kind("随便记一笔") == "other"


def test_work_priority():
    assert parser._work_priority("立即处理") == "urgent"
    assert parser._work_priority("尽快安排") == "high"
    assert parser._work_priority("不急") == "low"
    assert parser._work_priority("明天到期") == "normal"


def test_fingerprint_treats_int_and_float_and_key_order_the_same():
    from app.modules.quick_actions import service

    a = service.fingerprint("farm", "plot.create", {"name": "东一号田", "area_mu": 12.0})
    b = service.fingerprint("farm", "plot.create", {"area_mu": 12, "name": "东一号田"})
    assert a == b
