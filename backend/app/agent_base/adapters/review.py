"""Adapt host review management to a transport-independent review capability."""
import asyncio
from collections.abc import Awaitable, Callable
from app.agent_base.host_api.agents import ReviewManagerPort
from app.agent_base.host_api.data import JsonObject

from app.agent_base.host_api.contexts import ReviewPrompt


class ReviewAdapter:
    def __init__(self, manager: ReviewManagerPort, emit: Callable[[JsonObject], Awaitable[None]]) -> None:
        self.manager, self.emit = manager, emit

    async def ask(self, prompt: ReviewPrompt) -> str:
        request = self.manager.submit(review_type=prompt.review_type, title=prompt.title,
            content=prompt.content, question=prompt.question, metadata=prompt.metadata)
        await self.emit({"event": "review", "review_id": request.id, "review_type": prompt.review_type,
            "title": prompt.title, "content": request.content, "question": request.question, "metadata": request.metadata})
        try:
            return await asyncio.wait_for(request.future, timeout=prompt.timeout_seconds)
        except asyncio.TimeoutError:
            await self.emit({"event": "review_timeout", "review_id": request.id,
                "review_type": prompt.review_type, "title": prompt.title, "timeout": prompt.timeout_seconds})
            raise
