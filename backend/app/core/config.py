"""Application configuration loaded from environment variables."""

# ── Fix OpenBLAS memory exhaustion on Windows ──────────
# Must be set BEFORE any library that pulls in numpy (pydantic, etc.)
import os as _os
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
_os.environ.setdefault("OMP_NUM_THREADS", "1")

import logging
from pydantic_settings import BaseSettings
from pydantic import Field, ValidationInfo, field_validator
from functools import lru_cache

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # DeepSeek LLM — API key MUST come from .env, never hardcoded
    deepseek_api_key: str = Field(
        ...,
        description="DeepSeek API key (required, set in .env file)",
    )
    deepseek_base_url: str = "https://api.deepseek.com"

    # Default model — also used as the "pro" tier (powerful, expensive)
    # Override via DEEPSEEK_MODEL in .env
    deepseek_model: str = "deepseek-v4-pro"

    # Lightweight / fast model (cheap, suitable for simple tasks)
    deepseek_model_flash: str = "deepseek-v4-flash"

    # Sub-agent model — used by tool-calling sub-agents (ReAct, pipeline stages, etc.)
    sub_agent_model: str = "deepseek-v4-flash"

    # Max tool-call rounds for the dev agent — complex tasks (e.g. source/UML
    # consistency checks) need more than the old 12-round cap
    agent_max_steps: int = 50
    agent_max_tool_calls: int = 100
    agent_max_repeated_tool_calls: int = 3
    agent_max_run_seconds: int = 600
    agent_max_total_tokens: int = 100000
    agent_llm_timeout_seconds: int = 120
    agent_context_max_tokens: int = 32768
    agent_context_output_reserve_tokens: int = 4096
    agent_context_max_history_tokens: int = 12000
    agent_context_max_history_turns: int = 12
    strict_production: bool = False

    @field_validator("deepseek_api_key")
    @classmethod
    def check_key_not_default(cls, v: str) -> str:
        """Reject known placeholder/default keys to catch misconfiguration."""
        prohibited_prefixes = ("sk-3b6b0eaa", "sk-your-", "your-", "placeholder", "changeme")
        v_lower = v.lower()
        for prefix in prohibited_prefixes:
            if v_lower.startswith(prefix):
                logger.warning(
                    "deepseek_api_key appears to be a placeholder or leaked default value. "
                    "Please set a valid key in backend/.env"
                )
                break
        return v

    # Internal API auth — token for frontend → backend calls
    internal_api_token: str = Field(
        default="",
        description="If set, frontend must include Authorization: Bearer <token> header",
    )

    # App
    app_name: str = "ArchitectCoder API"
    app_version: str = "1.0.0"
    debug: bool = True

    # File storage
    uml_dir: str = "../temp/uml_files"

    # Agent 可访问的工作区根目录，多个目录用逗号分隔。为空时使用
    # 仓库目录和 uml_dir；需要访问外部源码时显式配置此项。
    workspace_roots: str = ""

    # CORS
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
    ]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
