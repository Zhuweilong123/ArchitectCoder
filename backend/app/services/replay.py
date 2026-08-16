"""确定性回放驱动 — 离线重跑已记录的 agent 会话。

两种模式：
  - mock（默认，L1）：ReplayLLM 按序吐记录的 llm_response，MockToolRegistry 按序
    吐记录的 tool_result。零网络、零副作用。
  - rerun（L2）：用真实 LLM 重跑，但工具仍按记录 mock（含真实 tool schema，
    使模型能正常发起工具调用）。用于模型/提示词 A/B、度量漂移。

匹配策略：用「游标」按调用顺序顺序匹配（而非按内容哈希），
因为 LLM 非确定，但同一次回放中的调用顺序稳定。

已知边界：
  - trace 文件记录整个进程的全部 LLM 调用（步级 + 工具内部嵌套 + done 后异步内存归档），
    步级调用以「单段非空 span_path」区分，ReplayLLM 只消费这一类。
  - 原始运行中 tool_call 参数非法 JSON 时，ReAct 循环跳过工具但仍写 tool_result，
    此时 mock 的 tool 游标会错位（罕见）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ReplayExhausted(Exception):
    """trace 中的记录已耗尽，说明回放流程与记录不一致。"""


def _pick(events: list[dict], event_type: str) -> list[dict]:
    """按文件顺序取出某类事件。"""
    return [e for e in events if e.get("event_type") == event_type]


def _is_step_level(evt: dict) -> bool:
    """判断一条 llm 事件是否属于「agent 自身的步级调用」。

    trace 文件记录了整个进程的全部 LLM 调用：
      - agent 步级 ainvoke_with_tools：span_path 为单段（如 "DevAgent"）——要回放；
      - 工具内部嵌套调用（explore_project 摘要 / optimize_v2 两阶段）：
        span_path 含 "/"——工具被 mock，不消费；
      - done 后异步内存归档：span_path 为空或含 "/"——不消费。
    """
    sp = evt.get("span_path") or ""
    return bool(sp) and "/" not in sp


def _step_level_events(events: list[dict], event_type: str) -> list[dict]:
    """取出步级 llm 事件（llm_request / llm_response 均带 span_path）。"""
    return [e for e in _pick(events, event_type) if _is_step_level(e)]


def _step_level_tool_specs(events: list[dict]) -> list[dict]:
    """从首个步级 llm_request 里提取真实 tool schema（FC specs）。

    ReAct 循环每步用同一份 specs，故取首个步级请求即可。
    """
    for req in _step_level_events(events, "llm_request"):
        tools = req.get("tools")
        if tools:
            return tools
    return []


class ReplayLLM:
    """按序吐出记录的 LLM 响应的假 LLM。

    实现 ReActAgent FC 循环 / optimize_v2 依赖的最小接口：
      - ainvoke_with_tools(messages, tools, tool_choice, **kwargs) -> dict
      - ainvoke(messages, **kwargs) -> str
    """

    def __init__(self, events: list[dict]):
        self.provider = "replay"
        self.model = "replay"
        self._responses = _step_level_events(events, "llm_response")
        self._cursor = 0

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def total(self) -> int:
        return len(self._responses)

    def _next(self) -> dict:
        if self._cursor >= len(self._responses):
            raise ReplayExhausted(
                f"trace 仅记录 {len(self._responses)} 次 LLM 响应，已耗尽"
            )
        r = self._responses[self._cursor]
        self._cursor += 1
        return r

    async def ainvoke_with_tools(
        self, messages, tools=None, tool_choice="auto", **kwargs
    ) -> dict:
        r = self._next()
        return {
            "content": r.get("content") or "",
            "tool_calls": r.get("tool_calls"),
        }

    async def ainvoke(self, messages, **kwargs) -> str:
        r = self._next()
        return r.get("content") or ""


class MockToolRegistry:
    """按序吐出记录的 tool_result 的假工具注册表。

    get_openai_specs() 返回记录中的真实 tool schema（rerun 模式需要，
    否则真 LLM 拿不到工具定义，无法发起工具调用）。
    """

    def __init__(self, events: list[dict]):
        self._results = _pick(events, "tool_result")
        self._specs = _step_level_tool_specs(events)
        self._cursor = 0

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def total(self) -> int:
        return len(self._results)

    def get_openai_specs(self) -> list[dict]:
        return self._specs

    def get_tools_description(self) -> str:
        return ""

    def list_tools(self) -> list[str]:
        return []

    async def aexecute_tool_with_params(self, name: str, parameters: dict) -> str:
        if self._cursor >= len(self._results):
            raise ReplayExhausted(
                f"trace 仅记录 {len(self._results)} 次工具返回，已耗尽（请求 {name}）"
            )
        r = self._results[self._cursor]
        self._cursor += 1
        recorded_name = r.get("tool_name", "")
        if recorded_name and recorded_name != name:
            logger.warning(
                "[Replay] 工具名不一致：回放请求 %s，记录为 %s", name, recorded_name
            )
        return str(r.get("observation", ""))

    def __len__(self) -> int:
        return 1  # 非空，让 arun 走 FC 分支

    def __bool__(self) -> bool:
        return True


def _build_rerun_llm():
    """构建真实 LLM（rerun 模式）。"""
    from app.agent_base.core.llm import BaseAgentsLLM
    return BaseAgentsLLM.from_settings(temperature=0.3)


async def replay_agent_session(session_id: str, *, mode: str = "mock") -> dict:
    """按 session 重放整段 agent 会话，逐轮返回 final_answer 与记录的对比。

    Args:
        mode: "mock"（默认，全 mock 零网络）或 "rerun"（真调 LLM，工具仍 mock）。

    返回：
        turns: [{user_message, final_answer, recorded_answer, matches, error}]
        llm_calls / llm_total / tool_calls / tool_total / mode / all_matched
    """
    from app.services.trace_reader import read_trace
    from app.agent_base.agents.react_agent import ReActAgent

    if mode not in ("mock", "rerun"):
        raise ValueError(f"未知回放模式: {mode}（可选 mock / rerun）")

    data = read_trace(session_id)
    if data is None:
        raise ValueError(f"trace 不存在: {session_id}")
    events: list[dict] = data["events"]

    # ── 轮次划分：user_message 为界，done 归属当前轮 ──
    turns: list[dict] = []
    cur_msg = None
    cur_done = None
    for e in events:
        et = e.get("event_type")
        if et == "user_message":
            if cur_msg is not None:
                turns.append({"message": cur_msg, "recorded_answer": cur_done})
            cur_msg = e.get("message", "")
            cur_done = None
        elif et == "done" and cur_done is None:
            cur_done = e.get("answer", "")
    if cur_msg is not None:
        turns.append({"message": cur_msg, "recorded_answer": cur_done})

    if not turns:
        # 无 user_message（optimize_v2 独立 trace）：按单轮空消息处理
        done = next(
            (e.get("answer", "") for e in reversed(events)
             if e.get("event_type") == "done"),
            None,
        )
        turns = [{"message": "", "recorded_answer": done}]

    llm = ReplayLLM(events) if mode == "mock" else _build_rerun_llm()
    registry = MockToolRegistry(events)

    # max_steps 给足记录中的最大 step，避免回放过早截断
    max_step = max(
        (int(e.get("step")) for e in events
         if e.get("event_type") in ("agent_step", "tool_call", "tool_result")
         and isinstance(e.get("step"), int)),
        default=5,
    )

    agent = ReActAgent(
        name="replay",
        llm=llm,
        tool_registry=registry,
        max_steps=max(max_step, 5),
    )

    results = []
    for t in turns:
        try:
            answer = await agent.arun(t["message"])
            error = None
        except ReplayExhausted as exc:
            answer = ""
            error = str(exc)
        recorded = t["recorded_answer"]
        results.append({
            "user_message": t["message"],
            "final_answer": answer,
            "recorded_answer": recorded,
            "matches": (not error and recorded is not None and answer == recorded),
            "error": error,
        })

    llm_total = len(_step_level_events(events, "llm_response"))
    tool_total = len(_pick(events, "tool_result"))

    all_matched = all(r["matches"] for r in results)
    if mode == "mock":
        # mock 模式额外校验游标是否完整消费（回放流程与记录一致）
        all_matched = all_matched and llm.cursor == llm_total and registry.cursor == tool_total

    return {
        "session_id": session_id,
        "mode": mode,
        "turns": results,
        "llm_calls": getattr(llm, "cursor", None),
        "llm_total": llm_total,
        "tool_calls": registry.cursor,
        "tool_total": tool_total,
        "all_matched": all_matched,
    }
