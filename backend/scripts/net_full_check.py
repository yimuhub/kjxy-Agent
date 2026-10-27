"""全链路联网功能测试：对运行中的 dev 环境逐项打真实请求（make native-api 后执行）。

用法：cd backend && ../.venv/bin/python scripts/net_full_check.py
覆盖：搜索状态/真实搜索、web.search、web.fetch（含 SSRF 拦截）、GitHub 镜像检索、
一句话技能/插件/智能体生成（联网取证 + LLM）、LLM 供应商连通。
各消耗少量生成配额与 LLM token，用于发版前回归。

覆盖：内置搜索链路 / web.search / web.fetch 工具 / GitHub 检索与镜像 /
一句话技能·插件·智能体生成（联网取证 + LLM）/ LLM 供应商连通。
输出每项 PASS/FAIL 与关键证据。
"""

import asyncio
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def login() -> str:
    r = httpx.post(f"{BASE}/auth/login", json={"username": "yard_admin", "password": "yard123"}, timeout=10)
    token = r.json()["data"]["access_token"]
    return token


H = {}


def get(path, **kw):
    kw.setdefault("headers", H)
    return httpx.get(f"{BASE}{path}", timeout=kw.pop("timeout", 60), **kw)


def post(path, body=None, timeout=60):
    return httpx.post(f"{BASE}{path}", json=body or {}, headers=H, timeout=timeout)


async def tool_direct(code: str, params: dict):
    """经工具注册表执行（与生产运行同一路径：schema 校验→权限→风险闸→超时→审计）。"""
    from app.core.deps import YardContext
    from app.db.session import SessionLocal
    from app.modules.knowledge.service import register_knowledge_search_tool
    from app.modules.search.tools import register_search_tools
    from app.modules.tools.registry import tool_registry

    register_knowledge_search_tool()
    register_search_tools()

    db = SessionLocal()
    try:
        ctx = YardContext(yard_id="5c54a189-d6a1-423c-934f-29a6dcbf1679", user_id="u-net-test",
                          roles=["yard_admin"], request_id="req-net-test")
        return await tool_registry.execute(db, code, params, ctx, "req-net-test")
    finally:
        db.close()


def main() -> None:
    global H
    H = {"Authorization": f"Bearer {login()}"}

    # 1) 联网状态
    d = get("/search/status").json()["data"]
    record("联网状态 /search/status", d["mode"] == "online" and d["internet"] is True,
           f"mode={d['mode']} provider={d['provider']} internet={d['internet']}")

    # 2) 真实搜索
    t0 = time.time()
    d = post("/search/test", {"query": "玉米病虫害防治", "count": 3}, timeout=60).json()["data"]
    record("真实搜索 /search/test", d["result_count"] >= 1,
           f"provider={d['provider']} {d['result_count']}条 {d['latency_ms']}ms 首条={d['results'][0]['title'][:30] if d['results'] else '-'}")

    # 3) web.search 工具（注册表全链路）
    try:
        out = asyncio.run(tool_direct("web.search", {"query": "水稻稻瘟病防治", "count": 3}))
        hits = out.get("results") or []
        record("工具 web.search", len(hits) >= 1, f"{len(hits)}条 首条={hits[0]['url'][:60] if hits else '-'}")
    except Exception as e:
        record("工具 web.search", False, f"{type(e).__name__}: {e}")

    # 4) web.fetch 工具（真实抓公网页 + SSRF 校验路径）
    try:
        out = asyncio.run(tool_direct("web.fetch", {"url": "https://example.com"}))
        text = out.get("text") or ""
        record("工具 web.fetch", "example" in (out.get("url") or "") and len(text) > 10,
               f"title={out.get('title','')[:30]} 正文{len(text)}字")
    except Exception as e:
        record("工具 web.fetch", False, f"{type(e).__name__}: {e}")

    # 5) SSRF 防护（内网地址必须被拒）
    try:
        asyncio.run(tool_direct("web.fetch", {"url": "http://127.0.0.1:8000/"}))
        record("web.fetch SSRF 拦截", False, "内网地址未被拦截！")
    except Exception as e:
        record("web.fetch SSRF 拦截", "WEB_FETCH_BLOCKED" in str(getattr(e, "code", "")) or "禁止" in str(e),
               f"已拦截: {str(e)[:60]}")

    # 6) GitHub 检索（国内镜像；不可达时须优雅报错而非挂死）
    t0 = time.time()
    r = post("/market/github/search", {"query": "agriculture skill", "limit": 3}, timeout=90)
    ok = r.status_code == 200
    detail = f"{r.status_code} {time.time()-t0:.1f}s"
    if ok:
        items = r.json()["data"].get("items") or []
        detail += f" {len(items)}条"
    else:
        try:
            detail += " " + r.json()["error"]["code"]
        except Exception:
            pass
    record("GitHub 技能检索 /market/github/search", ok or r.status_code == 503, detail + "（不可达时 503 优雅降级也算过）")

    # 7) 一句话技能检索（本地 + 联网）
    d = post("/market/agent/request", {"utterance": "我需要一个病虫害防治的技能"}, timeout=90).json()["data"]
    record("一句话技能检索 /market/agent/request", isinstance(d.get("matches"), list),
           f"{len(d['matches'])}个候选 recommend={d['recommend']} offline={d.get('offline')}")

    # 8) 一句话生成技能（联网取证 + LLM 生成 → 私有草稿）
    r = post("/market/agent/generate", {"utterance": "做一个玉米螟虫害识别与防治技能", "kind": "skill", "use_web": True}, timeout=300)
    ok = r.status_code == 200
    detail = str(r.status_code)
    if ok:
        d = r.json()["data"]
        skill = d.get("skill") or {}
        detail = f"{skill.get('name')} visibility={skill.get('visibility')} status={skill.get('status')} 来源{len(d.get('sources') or [])}条 note={str(d.get('note') or '')[:40]}"
    else:
        try:
            detail += " " + r.json()["error"]["message"][:80]
        except Exception:
            detail += r.text[:100]
    record("一句话生成技能（联网+LLM）", ok, detail)

    # 9) 一句话生成插件（联网取证 + LLM → 待审插件）
    r = post("/market/agent/generate", {"utterance": "做一个先查知识库再给灌溉建议的插件", "kind": "plugin", "use_web": True}, timeout=300)
    ok = r.status_code == 200
    if ok:
        d = r.json()["data"]
        p = d.get("plugin") or {}
        detail = f"{p.get('name')} status={p.get('status')} steps={p.get('step_count')}"
    else:
        try:
            detail = r.json()["error"]["message"][:80]
        except Exception:
            detail = r.text[:100]
    record("一句话生成插件（联网+LLM）", ok, detail)

    # 10) 一句话生成智能体（v3.5：202 任务 → 轮询到完成）
    r = post("/agents/generate", {"utterance": "帮我做一个病虫害防治智能体，能查知识库并给防治建议", "visibility": "private", "use_web": True}, timeout=60)
    ok = r.status_code == 202
    if ok:
        task_id = r.json()["data"]["task_id"]
        final = None
        for _ in range(60):
            time.sleep(3)
            row = get(f"/tasks/{task_id}").json()["data"]
            if row["status"] in ("succeeded", "failed"):
                final = row
                break
        ok = final is not None and final["status"] == "succeeded"
        result = (final or {}).get("result_json") or {}
        detail = f"task={final['status'] if final else '超时'} 草稿={result.get('name')} 匹配{len(result.get('matched') or [])}项 待补{len(result.get('skill_requirements') or [])}项 来源{len(result.get('sources') or [])}条"
    else:
        detail = r.text[:100]
    record("一句话生成智能体（v3.5 全链路）", ok, detail)

    # 11) LLM 供应商连通（局域网 openai_compatible）
    r = post("/llm/providers/1334f1cb-368b-46fa-ac4a-52752ebcf386/test", {}, timeout=60)
    ok = r.status_code == 200
    try:
        d = r.json()["data"]
        detail = f"响应{d.get('latency_ms','?')}ms 回复={str(d.get('reply') or d.get('content') or '')[:40]}"
    except Exception:
        detail = r.text[:100]
    record("LLM 供应商连通测试", ok, detail)

    print("\n===== 汇总 =====")
    failed = [x for x in results if x[1] == "FAIL"]
    for name, st, detail in results:
        print(f"{st}  {name}")
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
