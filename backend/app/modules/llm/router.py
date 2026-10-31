"""LLM 状态接口 + 模型供应商管理路由 + 模型绑定路由 + 调用观测查询路由。"""

from fastapi import APIRouter

from app.core.responses import ok
from app.modules.llm.bindings_router import router as bindings_router
from app.modules.llm.official_router import router as official_router
from app.modules.llm.relay_router import router as relay_router
from app.modules.llm.service import get_status
from app.modules.llm.supplier_router import router as supplier_router
from app.modules.llm.traces_router import router as traces_router

router = APIRouter(prefix="/llm", tags=["llm"])

# 供应商管理路由（/llm/providers*），并入 /api/v1 挂载
router.include_router(supplier_router)
# 模型绑定与能力清单（/llm/bindings*、/llm/capabilities），v3.25
router.include_router(bindings_router)
# 中转站通道（/llm/relay/channels*），v3.33
router.include_router(relay_router)
# 官方模型通道（/llm/official/*），v3.33
router.include_router(official_router)
# 观测查询路由（/llm/traces、/llm/usage/summary），W4
router.include_router(traces_router)


@router.get("/status")
def llm_status():
    return ok(get_status().model_dump())
