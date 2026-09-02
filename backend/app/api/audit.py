"""Read-only audit event API."""

from fastapi import APIRouter

from app.services.audit_log import get_audit_logger

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
async def list_audit_events(run_id: str = "", limit: int = 100):
    return {"events": get_audit_logger().list(run_id=run_id, limit=limit)}
