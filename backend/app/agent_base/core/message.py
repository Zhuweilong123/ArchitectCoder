"""消息系统"""

from typing import Optional, Dict, Any, Literal
from datetime import datetime
from pydantic import BaseModel

# 定义消息角色的类型
MessageRole = Literal["user", "assistant", "system", "tool"]


class Message(BaseModel):
    """统一消息格式，对内丰富、对外兼容"""

    content: str
    role: MessageRole
    timestamp: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None

    def __init__(self, content: str, role: MessageRole, **kwargs):
        super().__init__(
            content=content,
            role=role,
            timestamp=kwargs.get("timestamp", datetime.now()),
            metadata=kwargs.get("metadata", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为 OpenAI API 兼容格式"""
        return {
            "role": self.role,
            "content": self.content,
        }

    def __str__(self) -> str:
        return f"[{self.role}] {self.content}"
