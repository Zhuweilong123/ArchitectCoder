"""Host-domain request data and shared model wire values."""
from typing import Literal, NotRequired, TypedDict
from shared.llm_types import (
    JsonValue as JsonValue,
    JsonObject as JsonObject,
    FunctionCall as FunctionCall,
    ModelToolCall as ModelToolCall,
    ModelMessage as ModelMessage,
    FunctionSchema as FunctionSchema,
    ToolSchema as ToolSchema,
    TokenUsage as TokenUsage,
    ModelResponse as ModelResponse,
    ModelCallOptions as ModelCallOptions,
)

ConversationRole = Literal['system', 'user', 'assistant', 'tool', 'summary']

class ConversationMessage(TypedDict):
    role: ConversationRole
    content: str
    metadata: NotRequired[JsonObject]

class TodoItem(TypedDict):
    content: str
    status: Literal['pending', 'in_progress', 'completed']
    kind: NotRequired[Literal['analysis', 'execution', 'verification']]
    acceptance: NotRequired[str]

class WorkspaceSnapshot(TypedDict, total=False):
    workspace_root: str
    source_root: str
    test_root: str
    design_root: str
    project_file: str
    project_files: list[str]
    workspace_roots: list[str]

class ChangedPath(TypedDict):
    path: str
    operation: NotRequired[str]

class ExecutionRejection(TypedDict):
    contribution_id: str
    message: str
    stop_reason: str
