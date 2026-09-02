"""只读 Agent 运行指标接口。"""

from fastapi import APIRouter

from app.services.agent_metrics import get_agent_metrics

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("")
async def metrics():
    return get_agent_metrics().snapshot()

