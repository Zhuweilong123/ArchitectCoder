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
from typing import Literal

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # DeepSeek LLM — API key MUST come from .env, never hardcoded
    deepseek_api_key: str = Field(
        ...,
        description="DeepSeek API key (required, set in .env file)",
    )
    deepseek_base_url: str = "https://api.deepseek.com"

    # Fixed coding model for every agent in a session. Override via
    # DEEPSEEK_MODEL in .env; application code must not route per message.
    deepseek_model: str = "deepseek-v4-pro"

    # Compatibility only: releases before the fixed-model policy accepted
    # SUB_AGENT_MODEL. Consume a stale deployment setting without using it so
    # upgrading does not prevent the backend from starting.
    legacy_sub_agent_model: str | None = Field(
        default=None,
        validation_alias="SUB_AGENT_MODEL",
        repr=False,
        description="Deprecated and ignored; all agents use DEEPSEEK_MODEL.",
    )

    # Max tool-call rounds for the dev agent — complex tasks (e.g. source/UML
    # consistency checks) need more than the old 12-round cap
    agent_max_steps: int = 50
    agent_max_tool_calls: int = 100
    agent_max_repeated_tool_calls: int = 3
    agent_max_run_seconds: int = 600
    agent_max_total_tokens: int = 200000
    # Reserve enough room to turn completed evidence into a final user-facing
    # answer.  This is a convergence guard, separate from the context limit.
    agent_token_finalization_reserve_tokens: int = 12000
    agent_convergence_tool_steps: int = 25
    agent_convergence_budget_ratio: float = 0.8
    agent_convergence_keep_recent_steps: int = 3
    # Keep structured evidence for all tool calls in a normal run so context
    # compaction never falls back to raw, high-volume tool observations.
    agent_evidence_max_records: int = 128
    agent_force_final_summary_on_step_limit: bool = True
    agent_final_summary_max_tokens: int = 3000
    agent_llm_timeout_seconds: int = 120
    # The configured model supports a 1M window.  Keep 128K as the default
    # active working set so the agent can retain substantially more evidence
    # without blindly injecting an entire long-lived session.
    agent_context_max_tokens: int = 131072
    agent_context_output_reserve_tokens: int = 8192
    agent_context_max_history_tokens: int = 88000
    agent_context_max_history_turns: int = 48
    agent_context_max_summary_tokens: int = 4000
    agent_context_max_react_steps: int = 24

    # Main-flow orchestration knobs. The planner is deliberately small and the
    # optional strategy worker is bounded so orchestration cannot consume the
    # task budget before the main agent starts.
    agent_planner_max_tokens: int = 1200
    agent_planner_timeout_seconds: float = 30.0
    agent_explorer_max_steps: int = 6
    agent_orchestration_enabled: bool = True
    agent_orchestrator_provider: str = "app.agent_base.orchestration.provider:create"

    # Command tools expose one Linux/POSIX contract.  Windows deployments use
    # the configured WSL distribution instead of asking the model to choose a
    # cmd/PowerShell/Linux dialect per command.
    agent_command_environment: Literal["auto", "wsl", "native_linux"] = "auto"
    agent_wsl_distribution: str = ""
    agent_wsl_executable: str = "wsl.exe"
    # Starting a stopped WSL2 VM can take longer than a typical command.  Keep
    # this separate from the much longer per-command timeout used by BashTool.
    agent_wsl_preflight_timeout_seconds: float = 20.0

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
