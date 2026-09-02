"""FastAPI application entry point."""

import os
import sys
import logging
os.environ.setdefault("PYTHONUTF8", "1")
sys.dont_write_bytecode = True  # Never generate __pycache__

# ── Logging config ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Suppress noisy library loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.auth import require_auth
from app.api.files import router as files_router
from app.api.llm import router as llm_router
from app.api.testhub import router as testhub_router
from app.services.agent_chat_ws import router as agent_chat_router
from app.api.optimize_v2 import router as optimize_v2_router
from app.api.trace import router as trace_router
from app.api.metrics import router as metrics_router
from app.api.evals import router as evals_router
from app.api.runs import router as runs_router
from app.api.audit import router as audit_router

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers — auth is required on LLM endpoints
app.include_router(files_router)
app.include_router(llm_router, dependencies=[Depends(require_auth)])
app.include_router(testhub_router, dependencies=[Depends(require_auth)])
app.include_router(agent_chat_router, prefix="/api")  # Agent chat WebSocket
app.include_router(optimize_v2_router, dependencies=[Depends(require_auth)])  # optimize_uml v2
app.include_router(trace_router, dependencies=[Depends(require_auth)])         # trace 浏览/读取
app.include_router(metrics_router, dependencies=[Depends(require_auth)])        # Agent metrics
app.include_router(evals_router, dependencies=[Depends(require_auth)])          # Evaluation MVP
app.include_router(runs_router, dependencies=[Depends(require_auth)])            # Durable harness runs
app.include_router(audit_router, dependencies=[Depends(require_auth)])           # Harness audit events

if settings.strict_production and (settings.debug or not settings.internal_api_token):
    raise RuntimeError("strict_production requires debug=false and internal_api_token")

if settings.strict_production:
    from app.agent_base.execution import ExecutionEnvironmentError, build_linux_command_executor

    try:
        build_linux_command_executor(settings).preflight()
    except ExecutionEnvironmentError as exc:
        raise RuntimeError(
            f"strict_production requires a ready Linux command environment: {exc}"
        ) from exc

# Ensure required directories exist
os.makedirs(settings.uml_dir, exist_ok=True)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": settings.app_version}


@app.get("/")
async def root():
    return {"message": f"Welcome to {settings.app_name}", "docs": "/api/docs"}


if __name__ == "__main__":
    import uvicorn
    # Override uvicorn's log format to include timestamps
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["fmt"] = "%(asctime)s | %(levelname)-7s | %(message)s"
    log_config["formatters"]["default"]["datefmt"] = "%Y-%m-%d %H:%M:%S"
    log_config["formatters"]["access"]["fmt"] = '%(asctime)s | %(client_addr)s - "%(request_line)s" %(status_code)s'
    log_config["formatters"]["access"]["datefmt"] = "%Y-%m-%d %H:%M:%S"
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=settings.debug, log_config=log_config)
