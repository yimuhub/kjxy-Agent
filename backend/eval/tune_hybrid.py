"""混合检索调参脚本：对比 vector vs hybrid（alpha∈{0.5,0.7,0.9}）在 smoke 集上的召回。

约定与正式评估集一致：case.expect.expected_doc_title_contains。
为隔离"排序质量"与阈值过滤的影响，调参时将 retrieval_min_score 临时置 0.0
（只测排名召回；阈值语义不变，见 docs/retrieval-tuning.md）。
"""

import json
import os
import sys

os.environ.setdefault("APP_SECRET_KEY", "tuning-secret-key-min-32-chars-xxxxxx")
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "mock")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings  # noqa: E402
from app.core.deps import YardContext  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.modules.knowledge.embedding import MockEmbeddingProvider  # noqa: E402
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument  # noqa: E402
from app.modules.knowledge.service import search  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

YARD = "t-tune"

DOCS = [
    (
        "小麦播种技术要点",
        "冬小麦适播期为10月5日至15日，播量每亩15至20公斤，播深3到5厘米。"
        "播前整地要求深耕25厘米以上，底肥施用磷酸二铵每亩20公斤、尿素10公斤。"
        "镇压保墒是保证出苗整齐的关键措施，播后及时镇压。",
    ),
    (
        "土壤墒情监测与灌溉管理",
        "土壤墒情监测采用时域反射法传感器，埋设深度20厘米和40厘米两层。"
        "当20厘米土层体积含水量低于18%时启动灌溉，灌溉定额每次30至40方每亩。"
        "滴灌带铺设间距60厘米，灌溉后2小时复测含水量。",
    ),
    (
        "苹果树修剪技术规程",
        "苹果树冬季修剪以疏枝为主，疏除过密枝、病虫枝和竞争枝。"
        "纺锤形树形保持中心干优势，侧枝开张角度80度左右。"
        "结果枝组每隔15厘米保留一个，大年结果后及时回缩更新。",
    ),
    (
        "水稻纹枯病防治方案",
        "水稻纹枯病主要危害叶鞘，病斑呈云纹状褐色，湿度大时向上扩展。"
        "防治适期为分蘖末期至孕穗期，亩用井冈霉素水剂100毫升兑水喷雾。"
        "合理密植、降低田间湿度可显著减轻发病。",
    ),
    (
        "设施番茄灰霉病综合防控",
        "设施番茄灰霉病在低温高湿条件下高发，果实发病形成灰色霉层。"
        "防控措施包括早晨放风排湿、摘除病叶病果并带出棚外深埋。"
        "药剂选用腐霉利烟剂熏棚，安全间隔期7天。",
    ),
    (
        "生猪非洲猪瘟生物安全防控",
        "非洲猪瘟病毒对外界环境抵抗力强，无有效疫苗可用。"
        "猪场实行全进全出，车辆人员入场前经洗消中心三级洗消。"
        "禁止使用泔水喂猪，饲料原料需高温制粒处理。",
    ),
    (
        "农药安全使用间隔期表",
        "农药安全间隔期指最后一次施药至作物收获的最短天数。"
        "吡虫啉在水稻上安全间隔期为14天，啶虫脒在苹果上为10天。"
        "施药时穿戴防护服口罩，施药后洗手更衣，空包装三洗后交回。",
    ),
    (
        "大棚温湿度自动调控系统",
        "大棚温湿度自动调控系统由传感器、控制器和执行机构组成。"
        "温度超过32摄氏度自动开启卷膜机和风机，湿度高于85%启动除湿风机。"
        "夜间保温被自动覆盖，系统支持手机远程查看与报警。",
    ),
    (
        "农产品溯源码使用指南",
        "农产品溯源码是记录产地、生产过程和检测信息的一物一码。"
        "生产主体在平台注册后按批次生成溯源码，打印粘贴于包装。"
        "消费者扫码可查看种植记录、农残检测报告和流转信息。",
    ),
    (
        "玉米螟虫绿色防控技术",
        "玉米螟虫以幼虫钻蛀茎秆危害，造成风折和减产。"
        "绿色防控采用赤眼蜂防治，每亩释放蜂卡3张，间隔7天释放一次。"
        "配合性诱剂诱杀成虫，减少化学农药使用量60%以上。",
    ),
    # 干扰文档：主题相近，考验排序区分度
    (
        "小麦病虫害防治手册",
        "小麦赤霉病在扬花期防治，亩用多菌灵可湿性粉剂100克喷雾。"
        "蚜虫发生初期用吡虫啉防治，注意轮换用药延缓抗性。"
        "小麦吸浆虫成虫期撒毒土防治，每亩用辛硫磷颗粒剂2公斤。",
    ),
    (
        "农药标签识读与配药",
        "农药标签标注有效成分含量、毒性级别和适用作物。"
        "配药时先加少量水溶解可湿性粉剂，再加足水量搅拌均匀。"
        "二次稀释法可提高混匀度，避免药害发生。",
    ),
]


def seed(db) -> None:
    provider = MockEmbeddingProvider()
    for i, (title, content) in enumerate(DOCS):
        doc = KnowledgeDocument(
            yard_id=YARD,
            title=title,
            storage_key=f"tune-key-{i}",
            mime_type="text/plain",
            source="seed",
            status="ready",
            checksum=f"tune-checksum-{i}",
        )
        db.add(doc)
        db.flush()
        db.add(
            KnowledgeChunk(
                yard_id=YARD,
                document_id=doc.id,
                chunk_index=0,
                content=content,
                trust_level="yard_material",
                embedding=provider.embed([content])[0],
            )
        )
    db.commit()


def recall(cases, mode, alpha=None):
    ctx = YardContext(yard_id=YARD, user_id="u1", roles=["member"], request_id="tune")
    old_alpha, old_min = settings.retrieval_alpha, settings.retrieval_min_score
    try:
        if alpha is not None:
            settings.retrieval_alpha = alpha
        settings.retrieval_min_score = 0.0  # 只测排名召回
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()
        seed(session)
        hits_total, exact, paraphrase = 0, [0, 0], [0, 0]
        for c in cases:
            res = search(session, ctx, c["question"], top_k=5, mode=mode)
            titles = [h.title for h in res]
            ok = any(c["expect"]["expected_doc_title_contains"] in t for t in titles)
            hits_total += ok
            idx = 0 if c["query_type"] == "exact" else 1
            (exact if idx == 0 else paraphrase)[0] += ok
            (exact if idx == 0 else paraphrase)[1] += 1
        return hits_total, len(cases), exact, paraphrase
    finally:
        settings.retrieval_alpha, settings.retrieval_min_score = old_alpha, old_min


def main():
    eval_path = os.path.join(os.path.dirname(__file__), "hybrid_smoke_eval_v1.json")
    with open(eval_path, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    print(f"cases={len(cases)}, candidates={len(DOCS)} docs")
    rows = []
    for mode, alpha in [("vector", None), ("hybrid", 0.5), ("hybrid", 0.7), ("hybrid", 0.9)]:
        got, total, exact, par = recall(cases, mode, alpha)
        label = "vector" if mode == "vector" else f"hybrid α={alpha}"
        rows.append((label, got, total, exact, par))
        print(
            f"{label:>14}  recall {got}/{total}={got/total:.2f}  "
            f"exact {exact[0]}/{exact[1]}  paraphrase {par[0]}/{par[1]}"
        )
    best = max(rows, key=lambda r: r[1])
    print("best:", best[0])


if __name__ == "__main__":
    main()
