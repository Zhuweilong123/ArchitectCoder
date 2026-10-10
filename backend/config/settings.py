"""Application configuration loaded from environment variables."""

# ── Fix OpenBLAS memory exhaustion on Windows ──────────
# Must be set BEFORE any library that pulls in numpy (pydantic, etc.)
import os as _os
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
_os.environ.setdefault("OMP_NUM_THREADS", "1")

import logging
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field, ValidationInfo, field_validator, model_validator
from functools import lru_cache
from typing import Literal

from .plugin_defaults import (
    DEFAULT_EVALS_PROVIDER,
    DEFAULT_KNOWLEDGE_GRAPH_PROVIDER,
    DEFAULT_DESIGN_CONTRACT_PROVIDER,
    DEFAULT_MEMORY_PROVIDER,
    DEFAULT_ORCHESTRATION_PROVIDER,
    DEFAULT_TRACE_PROVIDER,
    DEFAULT_SKILLS_PROVIDER,
)
from .plugin_catalog import (
    BUILTIN_PLUGIN_ROOT, scan_manifests, packaged_default, packaged_enabled,
    deployment_overrides, resolved_settings, legacy_manifests,
)

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # OpenAI-compatible LLM endpoint — credentials MUST come from .env.
    llm_api_key: str = Field(
        ...,
        description="LLM API key (required, set in .env file)",
    )
    llm_base_url: str = Field(
        ...,
        description="OpenAI-compatible LLM endpoint URL",
    )

    # Fixed coding model for every agent in a session. Override via
    # LLM_MODEL_ID in .env; application code must not route per message.
    llm_model_id: str = Field(
        ...,
        description="Model identifier accepted by the configured LLM endpoint",
    )

    # Compatibility only: releases before the fixed-model policy accepted
    # SUB_AGENT_MODEL. Consume a stale deployment setting without using it so
    # upgrading does not prevent the backend from starting.
    legacy_sub_agent_model: str | None = Field(
        default=None,
        validation_alias="SUB_AGENT_MODEL",
        repr=False,
        description="Deprecated and ignored; all agents use LLM_MODEL_ID.",
    )

    agent_max_tool_calls: int = 0  # 0 disables the overall tool-call limit.
    agent_max_run_seconds: int = 0  # 0 disables the overall run deadline.
    # One explicit request-context hard limit. Soft convergence and compaction
    # thresholds are ratios of this value so deployments can scale it once.
    agent_context_hard_limit_tokens: int = 256000
    agent_context_soft_threshold_ratio: float = 0.78125
    agent_context_compaction_threshold_ratio: float = 0.9
    agent_session_compression_model: str = "deepseek-flash"
    agent_session_compression_trigger_ratio: float = 0.7
    agent_session_compression_max_tokens: int = 4000
    # Reserve enough room to turn completed evidence into a final user-facing
    # answer.  This is a convergence guard, separate from the context limit.
    agent_token_finalization_reserve_tokens: int = 12000
    agent_convergence_max_stalled_rounds: int = 3
    agent_convergence_max_recovery_rounds: int = 2
    agent_convergence_repeat_action_threshold: int = 3
    # Keep structured evidence for all tool calls in a normal run so context
    # compaction never falls back to raw, high-volume tool observations.
    agent_evidence_max_records: int = 128
    agent_final_summary_max_tokens: int = 3000
    agent_llm_timeout_seconds: int = 300
    agent_llm_timeout_retries: int = 3  # Retries after the initial timed-out request.
    agent_context_max_history_turns: int = 48
    agent_context_max_summary_tokens: int = 4000

    # Cumulative input + output budget; request context uses the main policy.
    agent_subagent_per_run_execution_budget_tokens: int = 131072
    # Main-agent-managed subagent entry point. The optional orchestration layer
    # remains independently controlled by agent_orchestration_enabled.
    agent_main_subagent_enabled: bool = True
    # Optional architecture-aware scheduling. Disabling it preserves the
    # single-Agent flow; enabling it requires an available project graph.
    agent_orchestration_enabled: bool = packaged_enabled("orchestration")
    agent_orchestrator_provider: str = DEFAULT_ORCHESTRATION_PROVIDER
    agent_architecture_scheduling_max_workers: int = packaged_default("orchestration", "max_workers", 0)
    agent_architecture_scheduling_total_tokens: int = packaged_default("orchestration", "total_tokens", 0)
    agent_architecture_scheduling_worker_seconds: float = packaged_default("orchestration", "worker_seconds", 0.0)

    # Optional cross-task memory.  The core only depends on MemoryPort; the
    # concrete SQLite adapter is loaded dynamically so it can be disabled or
    # replaced without changing the Agent main loop.
    agent_memory_enabled: bool = packaged_enabled("memory")
    agent_memory_provider: str = DEFAULT_MEMORY_PROVIDER
    agent_memory_db_path: str = packaged_default("memory", "db_path", "")
    agent_memory_recall_top_k: int = packaged_default("memory", "recall_top_k", 0)
    agent_memory_recall_max_tokens: int = packaged_default("memory", "recall_max_tokens", 0)

    # Optional trace backend.  The Agent core only depends on the tracing
    # port; the default JSONL provider remains compatible with existing logs.
    agent_trace_enabled: bool = packaged_enabled("trace")
    agent_trace_provider: str = DEFAULT_TRACE_PROVIDER

    # Optional evaluation backend.  The local Eval MVP is the default;
    # external CI or hosted evaluation services can implement the same port.
    agent_evals_enabled: bool = packaged_enabled("evals")
    agent_evals_provider: str = DEFAULT_EVALS_PROVIDER

    # Optional knowledge-graph backend.  Application services depend on the
    # provider boundary; the default adapter keeps the existing local SQLite
    # implementation replaceable by a remote or domain-specific backend.
    # The same plugin switch controls both graph backend availability and
    # whether graph tools are exposed to the main Agent.
    agent_knowledge_graph_enabled: bool = packaged_enabled("knowledge_graph")
    agent_knowledge_graph_provider: str = DEFAULT_KNOWLEDGE_GRAPH_PROVIDER
    agent_knowledge_graph_db_path: str = packaged_default("knowledge_graph", "db_path", "")

    # Optional design/source/test contract collector.  The core only depends
    # on the ContractProvider port so alternative analyzers can be installed.
    agent_design_contract_enabled: bool = packaged_enabled("design_contract")
    agent_design_contract_provider: str = DEFAULT_DESIGN_CONTRACT_PROVIDER

    # Read-only on-demand skills; each Agent captures one provider catalog.
    agent_skills_enabled: bool = packaged_enabled("skills")
    agent_skills_provider: str = DEFAULT_SKILLS_PROVIDER
    plugin_plan_dir: str = str(Path(__file__).resolve().parents[1] / ".architectcoder" / "plugins")
    plugin_manifest_file: str = ""
    # Additional roots; the repository extensions root is always scanned.
    plugin_roots: list[str] = Field(default_factory=list)
    plugin_config_file: str = ""

    @model_validator(mode="after")
    def resolve_plugin_configuration(self):
        declarations = scan_manifests((BUILTIN_PLUGIN_ROOT, *self.plugin_roots))
        extra = legacy_manifests(self.plugin_manifest_file)
        if {item["id"] for item in declarations} & {item["id"] for item in extra}:
            raise ValueError("Legacy plugin list duplicates a scanned plugin ID")
        declarations = (*declarations, *extra)
        overrides = deployment_overrides(self.plugin_config_file, {item["id"] for item in declarations})
        effective = resolved_settings(self, declarations, overrides)
        # Keep the original explicit field set so later discovery can distinguish
        # environment overrides from default values inherited from manifests.
        self.__dict__.update(effective.__dict__)
        return self

    # Command execution is selected by the runtime.  ``auto`` uses the native
    # host environment; WSL is an explicit compatibility option for projects
    # that require Linux on Windows.
    agent_command_environment: Literal["auto", "native_windows", "native_posix", "native_linux", "wsl"] = "auto"
    agent_wsl_distribution: str = ""
    agent_wsl_executable: str = "wsl.exe"
    # Starting a stopped WSL2 VM can take longer than a typical command.  Keep
    # this separate from the much longer per-command timeout used by ShellTool.
    agent_wsl_preflight_timeout_seconds: float = 20.0

    # Resolved project tasks use the local broker by default.  ``container``
    # opts into the Docker worker only when the deployment has pre-pulled an
    # image containing the project's toolchain.  ``wsl`` remains conservative
    # and fails closed unless isolation capabilities are explicitly provided
    # by a future worker implementation.
    agent_execution_worker: Literal["local", "wsl", "container"] = "local"
    # Combined stdout/stderr collection limit per command, enforced while reading.
    agent_command_output_limit_bytes: int = 10 * 1024 * 1024
    agent_container_image: str = "ubuntu:24.04"
    agent_container_executable: str = "docker"
    agent_container_preflight_timeout_seconds: float = 10.0
    agent_container_require_digest: bool = False
    # Toolchain version attestation is observational by default.  Production
    # deployments can promote mismatches to warnings or hard blocks.
    agent_toolchain_version_policy: Literal["off", "observe", "warn", "block"] = "observe"
    agent_toolchain_version_match_mode: Literal["compatible", "exact"] = "compatible"

    strict_production: bool = False

    @field_validator("llm_api_key")
    @classmethod
    def check_key_not_default(cls, v: str) -> str:
        """Reject known placeholder/default keys to catch misconfiguration."""
        prohibited_prefixes = ("sk-3b6b0eaa", "sk-your-", "your-", "placeholder", "changeme")
        v_lower = v.lower()
        for prefix in prohibited_prefixes:
            if v_lower.startswith(prefix):
                logger.warning(
                    "llm_api_key appears to be a placeholder or leaked default value. "
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
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8001, ge=1, le=65535)

    # File storage
    runtime_dir: str = "../temp"
    project_dir: str = "../project"
    # Compatibility alias for older diagram and directory APIs.
    uml_dir: str = "../project"

    @field_validator("uml_dir", "project_dir", "runtime_dir", mode="after")
    @classmethod
    def resolve_uml_dir(cls, value: str) -> str:
        """Resolve relative storage paths from the backend directory.

        The backend is launched from both ``backend/`` and the repository
        root by different entry points. Resolving here keeps UML, trace,
        eval and audit artifacts on the same stable runtime tree.
        Absolute paths remain explicit deployment overrides.
        """
        path = Path(value)
        if path.is_absolute():
            return str(path)
        backend_dir = Path(__file__).resolve().parents[1]
        return str((backend_dir / path).resolve())

    # Agent 可访问的工作区根目录，多个目录用逗号分隔。为空时使用
    # 仓库目录和项目目录；需要访问外部源码时显式配置此项。
    workspace_roots: str = ""

    # CORS
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
    ]

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "populate_by_name": True,
        "extra": "ignore",
    }

    @property
    def agent_context_soft_limit_tokens(self) -> int:
        return max(1, int(self.agent_context_hard_limit_tokens * self.agent_context_soft_threshold_ratio))


@lru_cache()
def get_settings() -> Settings:
    return Settings()
