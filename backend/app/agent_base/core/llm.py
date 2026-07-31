"""
BaseAgents 统一 LLM 接口

支持多 provider 的 LLM 调用层，兼容 OpenAI 标准 API。
自动检测 provider 或手动指定。

Usage::

    from app.agent_base.core.llm import BaseAgentsLLM

    llm = BaseAgentsLLM()                    # 自动检测
    llm = BaseAgentsLLM(provider="openai")   # 手动指定
    response = await llm.invoke(messages)
"""

import os
import logging
from typing import Optional, Iterator, AsyncIterator
from openai import OpenAI, AsyncOpenAI

logger = logging.getLogger(__name__)


class BaseAgentsLLM:
    """BaseAgents 统一 LLM 客户端

    封装 OpenAI 兼容的 LLM 调用，支持：
    - 多 provider: openai / deepseek / modelscope / zhipu / ollama / vllm
    - 同步 + 异步调用
    - 流式输出
    - 自动 provider 检测
    """

    # ── Provider 默认配置 ──────────────────────────────────
    PROVIDER_CONFIGS: dict[str, dict] = {
        "openai": {
            "env_key": "OPENAI_API_KEY",
            "default_base_url": "https://api.openai.com/v1",
            "default_model": "gpt-3.5-turbo",
        },
        "deepseek": {
            "env_key": "DEEPSEEK_API_KEY",
            "env_base_url": "DEEPSEEK_BASE_URL",
            "env_model": "DEEPSEEK_MODEL",
            "default_base_url": "https://api.deepseek.com",
            "default_model": "deepseek-chat",
        },
        "modelscope": {
            "env_key": "MODELSCOPE_API_KEY",
            "default_base_url": "https://api-inference.modelscope.cn/v1/",
            "default_model": "Qwen/Qwen2.5-72B-Instruct",
        },
        "zhipu": {
            "env_key": "ZHIPU_API_KEY",
            "default_base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "default_model": "glm-4",
        },
        "ollama": {
            "env_key": None,
            "default_base_url": "http://localhost:11434/v1",
            "default_model": "llama3",
        },
        "vllm": {
            "env_key": None,
            "default_base_url": "http://localhost:8000/v1",
            "default_model": "",
        },
    }

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: Optional[str] = "auto",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: int = 120,
        **kwargs,
    ):
        # 1. 确定 provider
        self.provider = (
            self._auto_detect_provider(api_key, base_url)
            if provider == "auto"
            else provider
        )

        # 2. 解析凭证
        resolved_key, resolved_url = self._resolve_credentials(api_key, base_url)

        # 3. 设置实例属性
        self.api_key = resolved_key
        self.base_url = resolved_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

        # 4. 确定模型 — 优先级：显式传参 > provider专用env > LLM_MODEL_ID > 默认值
        if model:
            self.model = model
        else:
            cfg = self.PROVIDER_CONFIGS.get(self.provider, {})
            env_model = cfg.get("env_model", "")
            self.model = (
                (env_model and os.getenv(env_model))
                or os.getenv("LLM_MODEL_ID")
                or cfg.get("default_model", "gpt-3.5-turbo")
            )

        # 5. 构建客户端
        self._client: OpenAI | None = None
        self._async_client: AsyncOpenAI | None = None
        if self.api_key is not None:
            self._client = OpenAI(
                api_key=self.api_key or "not-needed",
                base_url=self.base_url,
                timeout=self.timeout,
            )
            self._async_client = AsyncOpenAI(
                api_key=self.api_key or "not-needed",
                base_url=self.base_url,
                timeout=self.timeout,
            )

        logger.info(
            "BaseAgentsLLM 初始化: provider=%s model=%s base_url=%s",
            self.provider, self.model, self.base_url,
        )

    # ── 工厂方法 ─────────────────────────────────────────

    @classmethod
    def from_settings(cls, settings=None, temperature: float = 0.7, max_tokens: Optional[int] = None, timeout: int = 120, **kwargs):
        """从项目的 ``Settings`` 对象创建实例（零配置对接现有体系）。

        自动读取 settings 中的 deepseek_api_key / deepseek_base_url / deepseek_model，
        无需手动传参或设置环境变量。

        Usage::

            from app.core.config import get_settings
            from app.agent_base import BaseAgentsLLM

            llm = BaseAgentsLLM.from_settings(get_settings())
        """
        if settings is None:
            # 延迟导入，避免循环依赖
            from app.core.config import get_settings
            settings = get_settings()

        return cls(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            **kwargs,
        )

    # ── Provider 自动检测 ───────────────────────────────────

    def _auto_detect_provider(self, api_key: Optional[str], base_url: Optional[str]) -> str:
        """按优先级自动检测 LLM provider"""
        # 优先级 1: 检查特定 provider 的环境变量
        for name, cfg in self.PROVIDER_CONFIGS.items():
            if cfg.get("env_key") and os.getenv(cfg["env_key"]):
                return name

        # 优先级 2: 根据 base_url 判断
        actual_base_url = base_url or os.getenv("LLM_BASE_URL")
        if actual_base_url:
            lower = actual_base_url.lower()
            if "api-inference.modelscope.cn" in lower:
                return "modelscope"
            if "open.bigmodel.cn" in lower:
                return "zhipu"
            if "api.deepseek.com" in lower:
                return "deepseek"
            if "api.openai.com" in lower:
                return "openai"
            if "localhost" in lower or "127.0.0.1" in lower:
                if ":11434" in lower:
                    return "ollama"
                if ":8000" in lower:
                    return "vllm"
                return "local"

        # 优先级 3: 辅助判断 — 密钥格式
        actual_api_key = api_key or os.getenv("LLM_API_KEY")
        if actual_api_key:
            if actual_api_key.startswith("ms-"):
                return "modelscope"

        # 默认
        return "auto"

    def _resolve_credentials(self, api_key: Optional[str], base_url: Optional[str]) -> tuple:
        """根据 provider 解析 api_key 和 base_url"""
        cfg = self.PROVIDER_CONFIGS.get(self.provider, {})

        # 解析 api_key
        resolved_key = api_key
        if not resolved_key and cfg.get("env_key"):
            resolved_key = os.getenv(cfg["env_key"])
        if not resolved_key:
            resolved_key = os.getenv("LLM_API_KEY")

        # 解析 base_url — 优先级：显式传参 > provider专用env > LLM_BASE_URL > 默认值
        resolved_url = base_url
        if not resolved_url:
            env_base = cfg.get("env_base_url", "")
            resolved_url = (env_base and os.getenv(env_base)) or os.getenv("LLM_BASE_URL") or cfg.get("default_base_url", "")

        return resolved_key, resolved_url

    # ── 同步调用 ───────────────────────────────────────────

    def invoke(self, messages: list[dict], **kwargs) -> str:
        """同步调用 LLM，返回文本响应"""
        if self._client is None:
            raise RuntimeError(
                "BaseAgentsLLM client未初始化，请配置有效的 api_key。"
                f"当前 provider={self.provider} 需要有效的 API key。"
            )

        call_kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", self.temperature),
        )
        if kwargs.get("max_tokens", self.max_tokens):
            call_kwargs["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)
        if kwargs.get("json_mode"):
            call_kwargs["response_format"] = {"type": "json_object"}

        response = self._client.chat.completions.create(**call_kwargs)
        return response.choices[0].message.content or ""

    def think(self, messages: list[dict], **kwargs) -> Iterator[str]:
        """同步流式调用，逐块产出文本"""
        if self._client is None:
            raise RuntimeError("BaseAgentsLLM client未初始化，请配置有效的 api_key。")

        call_kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", self.temperature),
            stream=True,
        )
        if kwargs.get("max_tokens", self.max_tokens):
            call_kwargs["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)

        stream = self._client.chat.completions.create(**call_kwargs)
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    # ── 异步调用 ───────────────────────────────────────────

    async def ainvoke(self, messages: list[dict], **kwargs) -> str:
        """异步调用 LLM，返回文本响应"""
        if self._async_client is None:
            raise RuntimeError("BaseAgentsLLM async client未初始化，请配置有效的 api_key。")

        call_kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", self.temperature),
        )
        if kwargs.get("max_tokens", self.max_tokens):
            call_kwargs["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)
        if kwargs.get("json_mode"):
            call_kwargs["response_format"] = {"type": "json_object"}
        if kwargs.get("timeout"):
            call_kwargs["timeout"] = kwargs["timeout"]

        response = await self._async_client.chat.completions.create(**call_kwargs)
        return response.choices[0].message.content or ""

    async def athink(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        """异步流式调用，逐块产出文本"""
        if self._async_client is None:
            raise RuntimeError("BaseAgentsLLM async client未初始化，请配置有效的 api_key。")

        call_kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", self.temperature),
            stream=True,
        )
        if kwargs.get("max_tokens", self.max_tokens):
            call_kwargs["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)

        stream = await self._async_client.chat.completions.create(**call_kwargs)
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    # ── 工具调用 ───────────────────────────────────────────

    async def ainvoke_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
        **kwargs,
    ) -> dict:
        """异步调用 LLM，支持原生 Function Calling

        Returns:
            dict with ``content`` and ``tool_calls`` keys
        """
        if self._async_client is None:
            raise RuntimeError("BaseAgentsLLM async client未初始化。")

        call_kwargs = dict(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=kwargs.get("temperature", self.temperature),
        )
        if kwargs.get("max_tokens", self.max_tokens):
            call_kwargs["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)

        response = await self._async_client.chat.completions.create(**call_kwargs)
        msg = response.choices[0].message
        result: dict = {"content": msg.content, "tool_calls": None}
        if msg.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        return result

    def stream_invoke(self, messages: list[dict], **kwargs) -> Iterator[str]:
        """同步流式调用别名（与文档 API 兼容）"""
        yield from self.think(messages, **kwargs)
