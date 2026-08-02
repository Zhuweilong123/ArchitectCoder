"""ReActAgent — Reasoning + Acting 范式

支持两种运行模式:

1. **原生 Function Calling** (推荐, 默认) — ``await agent.arun(input)``
   利用 LLM 内置的工具调用能力，结构化 JSON 参数，支持多工具并行。

2. **文本解析降级** (兼容) — ``agent.run(input)``
   正则匹配 ``Thought:/Action:`` 文本格式，兼容不支持 FC 的模型。

Usage::

    # 推荐：异步 FC 模式
    agent = ReActAgent(name="推理助手", llm=llm, tool_registry=registry, max_steps=5)

    # 一次性获取结果
    result = await agent.arun("最近有什么关于AI的热点新闻？")

    # 流式获取每轮进度（用于前端实时展示）
    async for progress in agent.arun_stream("帮我优化这段代码"):
        print(f"Round {progress['step']}: {progress['actions']}")

    # 降级：同步文本解析模式
    result = agent.run("你好！")
"""

import json
import re
import logging
from typing import Optional, List, AsyncIterator

from ..core.agent import Agent
from ..core.llm import BaseAgentsLLM
from ..core.message import Message
from ..core.config import Config
from ..tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# ── Function Calling 模式 system prompt ─────────────────
FC_SYSTEM_PROMPT = """You are an AI assistant with reasoning and action capabilities.
You can call tools to fetch information, perform operations, analyze problems step by step, and finally give an accurate answer.

When using tools:
- You may call multiple independent tools at once (they run in parallel)
- Observe the results, then decide whether to continue calling tools
- When you have enough information, give the final answer directly (no tool calls)

Never fabricate answers. If the tool results are insufficient, continue using other tools or retry with adjusted parameters.
"""

# ── ReAct 文本模式提示词模板 (降级兼容) ──────────────────
REACT_PROMPT = """You are an AI assistant with reasoning and action capabilities. Analyze the problem by thinking, call the appropriate tools to gather information, and finally produce an accurate answer.

## Available tools
{tools}

## Workflow
Respond strictly in the following format, one step at a time:

Thought: Analyze the current problem; think about what information or action is needed.
Action: Choose one action in one of these formats:
- `{{tool_name}}[{{tool_input}}]` - call the specified tool
- `Finish[final answer]` - when you have enough information to answer

## Important notes
1. Every response must include both Thought and Action.
2. Tool call format must strictly follow: tool_name[parameters]
3. Only use Finish when you are confident you have enough information.
4. If tool results are insufficient, continue with other tools or different parameters of the same tool.

## Current task
**Question:** {question}

## Execution history
{history}

Now begin your reasoning and actions:
"""

# ── 流式 progress 中的 step 数据类 ──────────────────────

class ReActProgress:
    """单轮 ReAct 进度快照，通过 ``arun_stream()`` yield 给上层。

    Attributes:
        step: 当前轮次（1-based）
        actions: 本轮调用的工具名列表
        tool_calls_detail: ``[{name, arguments, observation}]`` 详情
        thought: LLM 文本内容（工具调用以外的思考部分）
        is_final: 是否为本轮后终止
        final_answer: 若 is_final 为 True，则为最终答案
    """

    __slots__ = (
        "step", "actions", "tool_calls_detail", "thought",
        "is_final", "final_answer",
    )

    def __init__(
        self,
        step: int,
        actions: list[str] | None = None,
        tool_calls_detail: list[dict] | None = None,
        thought: str = "",
        is_final: bool = False,
        final_answer: str = "",
    ):
        self.step = step
        self.actions = actions or []
        self.tool_calls_detail = tool_calls_detail or []
        self.thought = thought
        self.is_final = is_final
        self.final_answer = final_answer

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "actions": self.actions,
            "tool_calls_detail": self.tool_calls_detail,
            "thought": self.thought[:500],
            "is_final": self.is_final,
            "final_answer": self.final_answer,
        }


class ReActAgent(Agent):
    """ReAct (Reasoning + Acting) Agent

    核心循环:
    1. 构建 messages → 2. LLM 推理 (FC 或文本) →
    3. 解析 tool_calls / Thought-Action → 4. 执行工具 →
    5. 观察结果 → 回到 1 或返回最终答案

    Attributes:
        max_steps: 最大循环步数，防止无限循环
        use_native_fc: 是否使用原生 Function Calling（默认 True）
        custom_prompt: 自定义提示词模板（仅文本模式使用）
    """

    def __init__(
        self,
        name: str,
        llm: BaseAgentsLLM,
        tool_registry: ToolRegistry,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_steps: int = 5,
        use_native_fc: bool = True,
        custom_prompt: Optional[str] = None,
    ):
        super().__init__(name, llm, system_prompt, config)
        self.tool_registry = tool_registry
        self.max_steps = max_steps
        self.use_native_fc = use_native_fc
        self.current_history: List[str] = []
        self.prompt_template = custom_prompt or REACT_PROMPT
        logger.info(
            "✅ %s 初始化完成，最大步数: %d，FC模式: %s",
            name, max_steps, "启用" if use_native_fc else "禁用（文本解析）",
        )

    # ═══════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════

    async def arun(self, input_text: str, **kwargs) -> str:
        """异步运行 ReAct 循环（推荐入口）。

        当 ``use_native_fc=True`` 且有工具注册时使用原生 Function Calling；
        否则降级到同步文本解析 ``run()``。
        """
        if self.use_native_fc and self.tool_registry:
            return await self._arun_with_fc(input_text, **kwargs)
        logger.info("⚡ %s 降级到文本解析模式", self.name)
        return self.run(input_text, **kwargs)

    async def arun_stream(self, input_text: str, **kwargs) -> AsyncIterator[ReActProgress]:
        """流式运行 ReAct 循环 — 每轮 yield :class:`ReActProgress`。

        用于前端实时展示、编排层监控等需要逐轮获取进度的场景。

        当 ``use_native_fc=False`` 时，自动降级为仅 yield 最终结果。
        """
        if self.use_native_fc and self.tool_registry:
            async for progress in self._arun_with_fc_stream(input_text, **kwargs):
                yield progress
        else:
            # 降级：同步 run() 只产出一个最终结果
            result = self.run(input_text, **kwargs)
            yield ReActProgress(step=1, is_final=True, final_answer=result)

    def run(self, input_text: str, **kwargs) -> str:
        """同步运行 ReAct 循环（文本解析模式，向后兼容）。

        使用 Thought:/Action: 正则解析。保留给不支持 FC 的模型。
        """
        self.current_history = []
        current_step = 0

        logger.info("\n🤖 %s 开始处理问题: %s", self.name, input_text)

        while current_step < self.max_steps:
            current_step += 1
            logger.info("\n--- 第 %d 步 ---", current_step)

            # 1. 构建提示词
            tools_desc = self.tool_registry.get_tools_description()
            history_str = "\n".join(self.current_history)
            prompt = self.prompt_template.format(
                tools=tools_desc,
                question=input_text,
                history=history_str,
            )

            # 2. 调用 LLM
            messages = [{"role": "user", "content": prompt}]
            response_text = self.llm.invoke(messages, **kwargs)

            # 3. 解析输出
            thought, action = self._parse_output(response_text)
            logger.info("  Thought: %s", thought[:100] if thought else "无")
            if action:
                logger.info("  Action: %s", action)

            # 4. 检查是否完成
            if action and action.startswith("Finish"):
                final_answer = self._parse_action_input(action)
                self.add_message(Message(input_text, "user"))
                self.add_message(Message(final_answer, "assistant"))
                logger.info("🏁 %s 完成", self.name)
                return final_answer

            # 5. 执行工具调用
            if action:
                tool_name, tool_input = self._parse_action(action)
                if tool_name:
                    observation = self.tool_registry.execute_tool(tool_name, tool_input)
                    self.current_history.append(f"Step {current_step}: Action: {action}")
                    self.current_history.append(f"Step {current_step}: Observation: {observation}")
                    logger.info("  Observation: %s", observation[:100])
                else:
                    self.current_history.append(f"Step {current_step}: 无效的Action格式")
            else:
                self.current_history.append(f"Step {current_step}: 未解析到Action")

        # 达到最大步数
        final_answer = "抱歉，我无法在限定步数内完成这个任务。"
        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_answer, "assistant"))
        logger.warning("⚠️ %s 达到最大步数 %d", self.name, self.max_steps)
        return final_answer

    # ═══════════════════════════════════════════════════════
    # Function Calling 核心
    # ═══════════════════════════════════════════════════════

    def _build_fc_system_prompt(self) -> str:
        """构建 FC 模式的 system prompt。"""
        base = self.system_prompt or FC_SYSTEM_PROMPT
        return base

    async def _arun_with_fc(self, input_text: str, **kwargs) -> str:
        """一次性 FC 循环 — 收集流式输出，返回最终答案。"""
        final_answer = ""
        async for progress in self._arun_with_fc_stream(input_text, **kwargs):
            if progress.is_final:
                final_answer = progress.final_answer
        if not final_answer:
            final_answer = f"抱歉，在 {self.max_steps} 步内未能完成任务。"
        return final_answer

    async def _arun_with_fc_stream(
        self, input_text: str, **kwargs
    ) -> AsyncIterator[ReActProgress]:
        """原生 Function Calling 驱动的流式主循环。

        每轮 yield :class:`ReActProgress` — 包含步骤号、工具调用详情、
        思考内容、是否为最终轮。

        流程:
        1. 构建 messages（system + user）
        2. 调用 llm.ainvoke_with_tools(tool_specs)
        3. 遍历 tool_calls → 全部执行（支持多工具并行）
        4. yield ReActProgress → 追加 assistant + tool 消息
        5. 重复直到模型返回纯文本或达到 max_steps
        """
        from app.services.chat_trace import trace_span

        tool_specs = self.tool_registry.get_openai_specs()
        messages: list[dict] = [
            {"role": "system", "content": self._build_fc_system_prompt()},
            {"role": "user", "content": input_text},
        ]

        # 注入已有的对话历史（多轮场景）
        for msg in self._history:
            messages.append({"role": msg.role, "content": msg.content})

        self.current_history = []
        no_tool_call_streak = 0

        for step in range(1, self.max_steps + 1):
            logger.info("\n--- FC 第 %d/%d 步 ---", step, self.max_steps)

            # 1. 调用 LLM（带工具 schemas）
            with trace_span(f"{self.name}"):
                response = await self.llm.ainvoke_with_tools(
                    messages=messages,
                    tools=tool_specs,
                    tool_choice="auto",
                    temperature=kwargs.get("temperature", 0.3),
                )

            tool_calls = response.get("tool_calls")
            content = response.get("content") or ""

            # 2. 无 tool_calls → 纯文本回复
            if not tool_calls:
                no_tool_call_streak += 1
                self.current_history.append(
                    f"Step {step}: 模型返回纯文本 ({len(content)} 字符)"
                )
                logger.info("  → 无工具调用，streak=%d，内容预览: %s",
                           no_tool_call_streak, content[:120])

                messages.append({"role": "assistant", "content": content})

                # 模型本轮直接给出实质回复（未调用工具）→ 这就是最终答案。
                # 不要求 streak>=2 或 tool_executed，避免"你好"这类问候被循环
                # 逼着再走一步（继续问模型"下一步做什么"）而触发无意义的工具调用。
                if content.strip():
                    logger.info("🏁 %s FC 完成（模型直接回复）", self.name)
                    yield ReActProgress(
                        step=step, thought=content,
                        is_final=True, final_answer=content,
                    )
                    break

                # 空内容：提示模型使用工具
                if step == 1:
                    messages.append({
                        "role": "user",
                        "content": "Please call appropriate tools to answer the question. "
                                   "If you need more information, you may call tools multiple times.",
                    })
                    yield ReActProgress(
                        step=step, thought="(empty)", actions=[],
                        is_final=False,
                    )
                else:
                    yield ReActProgress(
                        step=step, thought=content, actions=[],
                        is_final=False,
                    )
                continue

            # 3. 有 tool_calls → 全部执行
            no_tool_call_streak = 0
            tool_results: list[dict] = []
            actions: list[str] = []
            details: list[dict] = []

            for tc in tool_calls:
                fn = tc["function"]
                tool_name = fn["name"]

                # 解析参数
                try:
                    tool_args = json.loads(fn["arguments"])
                except json.JSONDecodeError:
                    err_obs = (
                        f"Invalid JSON arguments for '{tool_name}'. "
                        f"Raw: {fn.get('arguments', '')[:200]}. "
                        f"Please re-send with valid JSON."
                    )
                    self.current_history.append(
                        f"Step {step}: {tool_name} → JSON解析失败"
                    )
                    tool_results.append({
                        "tool_call_id": tc["id"],
                        "content": err_obs,
                    })
                    actions.append(tool_name)
                    details.append({
                        "name": tool_name,
                        "arguments": fn.get("arguments", ""),
                        "observation": err_obs,
                    })
                    continue

                # 执行工具
                with trace_span(f"{self.name}/{tool_name}"):
                    result = await self.tool_registry.aexecute_tool_with_params(
                        tool_name, tool_args,
                    )
                observation_full = str(result)
                observation_short = observation_full[:2000]

                self.current_history.append(
                    f"Step {step}: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})"
                    f" → {observation_short[:150]}"
                )
                tool_results.append({
                    "tool_call_id": tc["id"],
                    "content": observation_short,
                })
                actions.append(tool_name)
                details.append({
                    "name": tool_name,
                    "arguments": tool_args,
                    "observation": observation_full,
                })
                logger.info("  🔧 %s(%s) → %s",
                           tool_name,
                           json.dumps(tool_args, ensure_ascii=False)[:80],
                           observation[:80])

            # 4. 追加 assistant + tool 消息到对话
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"],
                })

            yield ReActProgress(
                step=step,
                actions=actions,
                tool_calls_detail=details,
                thought=content,
                is_final=False,
            )

        # ── 最终处理 — 从 messages 中提取最后一条 assistant 内容 ──
        final_answer = ""
        for msg in reversed(messages):
            if msg["role"] == "assistant" and msg.get("content"):
                final_answer = msg["content"]
                break

        if not final_answer:
            final_answer = f"抱歉，在 {self.max_steps} 步内未能完成任务。"

        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_answer, "assistant"))
        logger.info("🏁 %s FC 完成 (%d 字符)", self.name, len(final_answer))

        # 除了已 yield 的 progress 外，不再额外 yield
        # — 调用方已经拿到了最终答案

    # ═══════════════════════════════════════════════════════
    # 文本解析 (降级兼容)
    # ═══════════════════════════════════════════════════════

    def _parse_output(self, text: str) -> tuple:
        """解析 LLM 输出，提取 (Thought, Action)"""
        thought = None
        action = None

        thought_match = re.search(r"Thought:\s*(.+?)(?=\n\s*(?:Action:|$))", text, re.DOTALL)
        if thought_match:
            thought = thought_match.group(1).strip()

        action_match = re.search(r"Action:\s*(.+)", text)
        if action_match:
            action = action_match.group(1).strip()

        return thought, action

    def _parse_action(self, action_text: str) -> tuple:
        """解析 Action 文本，提取 (tool_name, tool_input)"""
        # Format: tool_name[tool_input]  or  Finish[final_answer]
        match = re.match(r"(\w+)\[(.*)\]", action_text)
        if match:
            return match.group(1), match.group(2)
        return None, None

    def _parse_action_input(self, action_text: str) -> str:
        """解析 Finish[answer] 中的最终答案"""
        match = re.match(r"Finish\[(.*)\]", action_text, re.DOTALL)
        if match:
            return match.group(1)
        return action_text
