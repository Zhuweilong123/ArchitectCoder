"""FastAPI application entry point."""

import os
import sys
import logging
os.environ.setdefault("PYTHONUTF8", "1")
sys.dont_write_bytecode = True  # Never generate __pycache__

# ── 项目根目录加入 sys.path ─────────────────────────
# 后端从 backend/ 启动，但 knowledge_graph / memory_system 位于项目根，
# 必须显式加入 sys.path 才能 `from knowledge_graph.* import ...`。
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

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
from app.api.pipeline import router as pipeline_router
from app.api.testhub import router as testhub_router
from app.services.agent_chat_ws import router as agent_chat_router

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

# Include routers — auth is required on LLM & pipeline endpoints
app.include_router(files_router)
app.include_router(llm_router, dependencies=[Depends(require_auth)])
app.include_router(pipeline_router)  # auth: WS endpoint checks token manually; HTTP routes use Depends
app.include_router(testhub_router, dependencies=[Depends(require_auth)])
app.include_router(agent_chat_router, prefix="/api")  # Agent chat WebSocket

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
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=settings.debug, log_config=log_config)
