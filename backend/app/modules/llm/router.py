"""LLM 状态接口 + 模型供应商管理路由。"""

from fastapi import APIRouter

from app.core.responses import ok
from app.modules.llm.service import get_status
from app.modules.llm.supplier_router import router as supplier_router

router = APIRouter(prefix="/llm", tags=["llm"])

# 供应商管理路由（/llm/providers*），并入 /api/v1 挂载
router.include_router(supplier_router)


@router.get("/status")
def llm_status():
    return ok(get_status().model_dump())
