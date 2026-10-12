"""Agent, prompt, progress and task capabilities exposed to plugins."""
from __future__ import annotations
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import NamedTuple, Protocol
from .data import ConversationMessage, JsonObject, ModelCallOptions, ModelMessage, ModelResponse, ToolSchema, TodoItem
from typing import Unpack

class ModelPort(Protocol):
    model: str
    provider: str
    async def ainvoke(self, messages: list[ModelMessage], **options: Unpack[ModelCallOptions]) -> str: ...
    async def ainvoke_with_tools(self, messages: list[ModelMessage], tools: list[ToolSchema],
                                tool_choice: str = 'auto', **options: Unpack[ModelCallOptions]) -> ModelResponse: ...

class ProgressSnapshotPort(Protocol):
    step: int
    actions: list[str]
    tool_calls_detail: list[JsonObject]
    thought: str
    is_final: bool
    final_answer: str

class ToolRegistryPort(Protocol):
    def get_openai_specs(self) -> list[ToolSchema]: ...
    def get_tools_description(self) -> str: ...
    def list_tools(self) -> list[str]: ...
    async def aexecute_tool_with_params(self, name: str, parameters: JsonObject) -> str: ...
    def __len__(self) -> int: ...

class AgentPort(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def llm(self) -> ModelPort: ...
    @property
    def tool_registry(self) -> ToolRegistryPort: ...
    last_run_checkpoint: JsonObject
    def arun_stream(self, input_text: str, context: str = '') -> AsyncIterator[ProgressSnapshotPort]: ...
    async def arun(self, input_text: str, context: str = '') -> str: ...
    def restore_history(self, messages: list[ConversationMessage]) -> None: ...

class ProgressPort(Protocol):
    @property
    def events(self) -> list[JsonObject]: ...
    def on_progress(self, callback: Callable[[JsonObject], None | Awaitable[None]]) -> None: ...
    def emit(self, event: JsonObject) -> None: ...
    def clear(self) -> None: ...

class PromptBuilderPort(Protocol):
    prompt_version: str
    static_prompt_report: JsonObject
    last_context_report: JsonObject
    async def build_context(self, project_file: str, source_dir: str, test_dir: str, user_message: str) -> str: ...

class ReviewRequestPort(Protocol):
    id: int
    review_type: str
    title: str
    content: str
    question: str
    metadata: JsonObject
    @property
    def future(self) -> Awaitable[str]: ...

class ReviewManagerPort(Protocol):
    approval_events: list[JsonObject]
    baseline: list[JsonObject] | None
    def submit(self, review_type: str, title: str = '', content: str = '', question: str = '',
               metadata: JsonObject | None = None) -> ReviewRequestPort: ...

class AgentAssembly(NamedTuple):
    """Named production assembly result; positional unpacking is retained for existing callers."""
    agent: AgentPort
    review_manager: ReviewManagerPort | None
    prompt_builder: PromptBuilderPort

class TaskBindingPort(Protocol):
    task_id: str
    run_id: str
    def sync(self, *, todos: list[TodoItem] | None = None, checkpoint: JsonObject | None = None) -> None: ...
    def finalize(self, result_status: str, *, checkpoint: JsonObject | None = None) -> None: ...

class EnvironmentPromptPort(Protocol):
    def to_prompt(self) -> str: ...
