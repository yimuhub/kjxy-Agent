#!/usr/bin/env python3
"""评估集骨架运行器（W4）：golden jsonl → 逐条 run_declarative_agent → 命中率报告。

输入行格式（backend/evals/golden_v1.jsonl，每行一个 JSON）::

    {"id": "...", "agent_code": "...", "question": "...",
     "expect_keywords": ["...", "..."], "note": "..."}

用法（backend/ 目录）::

    ../.venv/bin/python scripts/run_evals.py                        # 当前 LLM（CI=fake）验管道
    ../.venv/bin/python scripts/run_evals.py --supplier-id <uuid>   # 指定供应商真打模型
    ../.venv/bin/python scripts/run_evals.py --file evals/golden_v1.jsonl --yard-id <uuid>

口径说明（诚实边界）：
- **默认不动配置**：LLM 走 .env / LLM_PROVIDER（CI 与开发机默认 fake）——只验证
  管道通（agent 可解析、可运行、报告可产出），关键词命中率预期为 0 不代表线上质量；
- ``--supplier-id``：把该供应商设为小院 active 后再跑（**会改小院配置**，跑完请
  按需切回），此时 llm_traces.purpose=eval 可观测真实调用；
- 关键词判定为不区分大小写的子串命中；缺失 agent / 运行异常单独计错，不算命中。

产物：stdout 报告 + ``evals/reports/<UTC 时间戳>.md``。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_FILE = BACKEND_DIR / "evals" / "golden_v1.jsonl"
REPORT_DIR = BACKEND_DIR / "evals" / "reports"
REQUIRED_KEYS = ("id", "agent_code", "question", "expect_keywords", "note")


def load_items(path: Path) -> list[dict]:
    """解析 jsonl：空行跳过；缺字段立即报错（结构契约与 pytest 冒烟同一口径）。"""
    items: list[dict] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path.name} 第 {lineno} 行不是合法 JSON：{e}") from None
        missing = [k for k in REQUIRED_KEYS if k not in obj]
        if missing:
            raise SystemExit(f"{path.name} 第 {lineno} 行缺字段：{missing}")
        if not isinstance(obj["expect_keywords"], list):
            raise SystemExit(f"{path.name} 第 {lineno} 行 expect_keywords 必须是数组")
        items.append(obj)
    if not items:
        raise SystemExit(f"{path} 为空评估集")
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="科技小院评估集骨架运行器（W4）")
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="golden jsonl 路径")
    parser.add_argument("--yard-id", default=None, help="小院 id（默认取第一个小院）")
    parser.add_argument(
        "--supplier-id",
        default=None,
        help="把该供应商设为小院 active 后真打模型（会改小院配置）；缺省沿用当前 LLM 配置",
    )
    args = parser.parse_args(argv)

    # 重量导入全部延后：--help 在没有任何环境/依赖准备时也必须可用（CI 冒烟依赖此点）
    import asyncio
    import os

    sys.path.insert(0, str(BACKEND_DIR))
    os.environ.setdefault("LLM_PROVIDER", "fake")
    os.environ.setdefault("EMBEDDING_PROVIDER", "mock")

    from app.core.deps import YardContext
    from app.db.session import SessionLocal
    from app.modules.agents import declarative
    from app.modules.yards.models import Yard
    from sqlalchemy import select

    items = load_items(Path(args.file))
    db = SessionLocal()
    try:
        yard_id = args.yard_id
        if not yard_id:
            yard = db.scalar(select(Yard).order_by(Yard.created_at.asc()))
            if yard is None:
                raise SystemExit("库中没有任何小院，无法运行评估。")
            yard_id = yard.id

        active_note = "当前 LLM 配置（默认 fake 只验管道）"
        if args.supplier_id:
            from app.modules.llm import supplier_service

            supplier_service.set_active_supplier(db, yard_id, args.supplier_id)
            db.commit()
            active_note = f"小院 active 供应商已切换为 {args.supplier_id}（评估前会改配置）"

        results: list[dict] = []
        for item in items:
            row = {"id": item["id"], "agent_code": item["agent_code"], "status": "", "hits": []}
            agent = declarative.get_published_by_code(
                db, yard_id=yard_id, agent_code=str(item["agent_code"])
            )
            if agent is None:
                row["status"] = "missing_agent"
                results.append(row)
                continue
            ctx = YardContext(
                yard_id=yard_id,
                user_id="eval-runner",
                roles=["yard_admin"],
                request_id=f"eval-{item['id']}",
            )
            try:
                result = asyncio.run(
                    declarative.run_declarative_agent(
                        db,
                        agent=agent,
                        input_data={"question": str(item["question"])},
                        ctx=ctx,
                        task_id=f"eval-{item['id']}",
                        llm_purpose="eval",
                    )
                )
                db.commit()
            except Exception as e:  # noqa: BLE001 —— 单条失败不中断整轮评估
                db.rollback()
                row["status"] = f"error: {type(e).__name__}"
                results.append(row)
                continue
            answer = str(result.output.get("answer") or "")
            lowered = answer.lower()
            row["hits"] = [kw for kw in item["expect_keywords"] if str(kw).lower() in lowered]
            row["status"] = "ok"
            results.append(row)
    finally:
        db.close()

    # ---- 报告（stdout + markdown 落盘） ----
    from datetime import UTC, datetime

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    total_kw = sum(len(i["expect_keywords"]) for i in items)
    hit_kw = sum(len(r["hits"]) for r in results if r["status"] == "ok")
    hit_rate = (hit_kw / total_kw * 100) if total_kw else 0.0
    lines = [
        "# 评估集运行报告（golden_v1 骨架）",
        "",
        f"- 时间：{ts}（UTC）",
        f"- 评估集：{args.file}（{len(items)} 条）",
        f"- 模型口径：{active_note}",
        f"- 关键词命中率：{hit_kw}/{total_kw} = {hit_rate:.1f}%",
        "  （默认 fake 模型不产出领域文本，命中率低属预期——见脚本头部口径说明）",
        "",
        "| id | agent_code | 状态 | 命中关键词 |",
        "| --- | --- | --- | --- |",
    ]
    by_id = {i["id"]: i for i in items}
    for r in results:
        expect = by_id[r["id"]]["expect_keywords"]
        miss = [k for k in expect if k not in r["hits"]]
        lines.append(
            f"| {r['id']} | {r['agent_code']} | {r['status']} | "
            f"{'+'.join(r['hits']) or '—'}（未中：{'+'.join(miss) or '—'}） |"
        )
    report = "\n".join(lines) + "\n"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{ts}.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"报告已写入：{out}")
    failed = [r for r in results if r["status"] != "ok"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
