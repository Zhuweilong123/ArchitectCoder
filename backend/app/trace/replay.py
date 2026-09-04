"""确定性回放驱动 — 离线重跑已记录的 agent 会话。

三种模式：
  - mock（默认，L1）：ReplayLLM 按序吐记录的 llm_response，MockToolRegistry 按序
    吐记录的 tool_result。零网络、零副作用。
  - rerun（L2）：用真实 LLM 重跑，但工具仍按记录 mock（含真实 tool schema，
    使模型能正常发起工具调用）。用于模型/提示词 A/B、度量漂移。
  - live（L3）：真实 LLM + 混合工具（只读工具真实执行、其余 mock，见
    HybridToolRegistry）。让模型读到当前项目真实状态，度量「真实执行」下的漂移。

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
import os

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
      - done 后异步内存归档：span_path 为空或含 "/"——不消费；
      - 回放自身的调用：span_path 为 "replay"（rerun 模式真调 LLM 被写进 trace）——
        也排除，避免污染游标对齐。
    """
    sp = evt.get("span_path") or ""
    return bool(sp) and "/" not in sp and sp != "replay"


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


def _reconstruct_original_context(events: list[dict], first_turn_msg: str) -> tuple[str, str]:
    """从 trace 重建原始运行的 system prompt 与 workspace 上下文。

    rerun 模式真调 LLM，需要拿到与原始运行一致的初始上下文（system prompt +
    workspace/记忆/日期），否则模型不知工作区路径、可用工具与任务规则，轨迹会
    大幅漂移（如原始跑 glob/bash，rerun 却去 read_file）。

    trace 每条步级 llm_request 都记录了 system_prompt（拆出独立字段），并把
    workspace 上下文拼在首个 user 消息内容开头（`context + "\n\n" + 原始输入`）。
    故从首个步级 llm_request 即可还原：system_prompt 直接取，context 从首个
    user 内容里剥掉原始输入得到。

    返回 (system_prompt, context)；缺失时返回空串（回退泛型 prompt / 无上下文）。
    """
    reqs = _step_level_events(events, "llm_request")
    if not reqs:
        return "", ""
    system_prompt = reqs[0].get("system_prompt", "") or ""
    context = ""
    msgs = reqs[0].get("messages") or []
    if msgs and msgs[0].get("role") == "user":
        first_user_content = msgs[0].get("content", "") or ""
        if first_turn_msg and first_user_content.endswith(first_turn_msg):
            context = first_user_content[: -len(first_turn_msg)].rstrip("\n")
    return system_prompt, context


def _reconstruct_workspace(events: list[dict]) -> tuple[str, str, str, str]:
    """还原 (source_dir, test_dir, design_dir, project_file)。

    live 模式真实工具（read_file/glob/...）需要真实目录做 safe_path 守卫。
    优先级：user_message 事件记录（新 trace 带 source_dir/test_dir）→
    从首个步级 llm_request 的 context 文本解析（旧 trace 回退）。
    design_dir 由 project_file 推导（与 create_conversation_tools 同口径）。
    """
    source_dir = test_dir = project_file = ""
    for e in events:
        if e.get("event_type") == "user_message":
            source_dir = e.get("source_dir", "") or source_dir
            test_dir = e.get("test_dir", "") or test_dir
            project_file = e.get("project_file", "") or project_file

    # 旧 trace 回退：从首个步级 llm_request 的首条 user 内容解析 workspace 行
    if not (source_dir and test_dir):
        first_user_content = ""
        reqs = _step_level_events(events, "llm_request")
        if reqs:
            msgs = reqs[0].get("messages") or []
            if msgs and msgs[0].get("role") == "user":
                first_user_content = msgs[0].get("content", "") or ""
        for line in first_user_content.splitlines():
            s = line.strip()
            if s.startswith("- Source directory:") and not source_dir:
                source_dir = s.split(":", 1)[1].strip()
            elif s.startswith("- Test directory:") and not test_dir:
                test_dir = s.split(":", 1)[1].strip()

    # design_dir：project_file 所在目录（当前项目设计目录），否则全局 uml_dir
    if project_file and os.path.isfile(project_file):
        design_dir = os.path.dirname(os.path.abspath(project_file))
    else:
        try:
            from app.core.config import get_settings
            design_dir = os.path.abspath(get_settings().uml_dir)
        except Exception:
            design_dir = ""
    return source_dir, test_dir, design_dir, project_file


def _split_turn_events(events: list[dict]) -> list[list[dict]]:
    """按 user_message 边界切分事件流，返回每轮的事件列表（含该轮 user_message）。

    首个 user_message 之前的事件（如 session_start）不属于任何轮，跳过。
    无 user_message 时返回空列表（调用方按单轮处理）。
    """
    groups: list[list[dict]] = []
    cur: list[dict] | None = None
    for e in events:
        et = e.get("event_type")
        if et == "user_message":
            if cur is not None:
                groups.append(cur)
            cur = [e]
        elif cur is not None:
            cur.append(e)
    if cur is not None:
        groups.append(cur)
    return groups


def _extract_recorded_steps(turn_events: list[dict]) -> list[dict]:
    """从单轮事件重建「原始运行」的步级工具执行（供 rerun 左列对比）。

    依据 agent_step（步序/思考/动作/是否终步）与 tool_call + tool_result
    （span_id 关联，补齐参数与观察结果）还原，与回放 steps 同构：
    [{step, thought, actions, tool_calls, is_final}]，
    tool_calls 每项 {name, arguments, observation}。

    这些事件只由原始运行的 agent_chat_ws 写入，回放不写，故天然是原始侧。
    """
    steps: list[dict] = []
    cur: dict | None = None
    calls_by_span: dict[str, dict] = {}
    for e in turn_events:
        et = e.get("event_type")
        if et == "agent_step":
            if cur is not None:
                steps.append(cur)
            cur = {
                "step": e.get("step"),
                "thought": e.get("thought", "") or "",
                "actions": list(e.get("actions") or []),
                "tool_calls": [],
                "is_final": bool(e.get("is_final", False)),
            }
        elif et == "tool_call":
            tc = {
                "name": e.get("tool_name", ""),
                "arguments": e.get("arguments", {}),
                "observation": "",
            }
            if cur is not None:
                cur["tool_calls"].append(tc)
            calls_by_span[e.get("span_id")] = tc
        elif et == "tool_result":
            tc = calls_by_span.get(e.get("span_id"))
            if tc is not None:
                tc["observation"] = str(e.get("observation", "") or "")
    if cur is not None:
        steps.append(cur)
    return steps


def _suppress_trace_hook(kind: str, *args, **kwargs):
    """no-op trace 钩子：回放期间屏蔽 LLM 调用写入任何 trace。

    rerun 模式会真调 LLM，若全局 trace 钩子仍指向某会话，回放自身的调用
    （span_path="replay"）会被写进该会话的 trace 文件，污染后续回放的游标对齐。
    """
    return None


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

    def __init__(self, events: list[dict], *, graceful: bool = False):
        self._results = _pick(events, "tool_result")
        self._specs = _step_level_tool_specs(events)
        self._cursor = 0
        self._graceful = graceful

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
            if self._graceful:
                # rerun 模式：真 LLM 偏离原始轨迹（多调了工具），mock 无结果可用。
                # 返回占位观察而非抛错，让回放能继续产出最终答案（用于对比漂移）。
                return (
                    f"[回放] 记录的 {len(self._results)} 次工具返回已耗尽，"
                    f"模型本次轨迹偏离原始运行（额外请求 {name}），无法 mock 其真实结果。"
                )
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


class HybridToolRegistry:
    """L3 live 混合回放：真 LLM + 只读/文件系统工具真实执行、其余 mock。

    LLM 看到的工具集（get_openai_specs）返回完整记录 schema，决策空间与原运行
    一致（避免「工具可见性变化」混淆 A/B）；运行时按 real_policy 决定真实执行
    还是 mock。mock 侧按工具名分队列 pop 记录结果——真实工具不消费队列，
    交错调用不会错位（全局游标在混合执行下会错位）。
    """

    def __init__(
        self, events: list[dict], real_registry, real_policy: set[str],
        *, graceful: bool = True,
    ):
        self._real = real_registry
        self._real_policy = real_policy
        self._specs = _step_level_tool_specs(events)
        self._graceful = graceful
        self._by_tool: dict[str, list[str]] = {}
        for r in _pick(events, "tool_result"):
            self._by_tool.setdefault(r.get("tool_name", ""), []).append(
                str(r.get("observation", ""))
            )
        self._total = sum(len(v) for v in self._by_tool.values())
        self._calls = 0

    @property
    def cursor(self) -> int:
        return self._calls

    @property
    def total(self) -> int:
        return self._total

    def get_openai_specs(self) -> list[dict]:
        return self._specs

    def get_tools_description(self) -> str:
        return ""

    def list_tools(self) -> list[str]:
        return [s.get("function", {}).get("name", "") for s in self._specs]

    async def aexecute_tool_with_params(self, name: str, parameters: dict) -> str:
        self._calls += 1
        if name in self._real_policy:
            return await self._real.aexecute_tool_with_params(name, parameters)
        q = self._by_tool.get(name)
        if q:
            return q.pop(0)
        if self._graceful:
            return (
                f"[回放] 工具 {name} 的记录结果已耗尽，模型本次轨迹偏离原始运行，"
                f"无法 mock 其真实结果。"
            )
        raise ReplayExhausted(
            f"trace 记录的工具 {name} 结果已耗尽（请求 {name}）"
        )

    def __len__(self) -> int:
        return len(self._specs) or 1

    def __bool__(self) -> bool:
        return True


def _build_live_registry(
    events: list[dict], source_dir: str, test_dir: str, design_dir: str,
    *, tool_policy: str = "readonly",
):
    """构建 live 模式的混合工具注册表。

    readonly（默认）：read_file/glob 真实执行，其余（write/edit/bash/子代理）mock。
    full：A 层文件系统工具全部真实执行（写盘/跑命令有副作用，风险自负）；
         bash 不传 review_manager → 敏感命令 fail-closed、高危直接拒（安全阀）。
    """
    from app.agent_base.tools.registry import ToolRegistry
    from app.agent_base.tools.my_tools.file_system_tools import (
        ReadFileTool, GlobTool, WriteFileTool, EditFileTool, BashTool,
    )

    if tool_policy not in ("readonly", "full"):
        tool_policy = "readonly"

    real_policy = {"read_file", "glob"}
    if tool_policy == "full":
        real_policy |= {"write_file", "edit_file", "bash"}

    real = ToolRegistry()
    if "read_file" in real_policy:
        real.register_tool(ReadFileTool(source_dir, test_dir, design_dir))
    if "glob" in real_policy:
        real.register_tool(GlobTool(source_dir, test_dir, design_dir))
    if "write_file" in real_policy:
        real.register_tool(WriteFileTool(source_dir, test_dir, design_dir))
    if "edit_file" in real_policy:
        real.register_tool(EditFileTool(source_dir, test_dir, design_dir))
    if "bash" in real_policy:
        real.register_tool(BashTool(source_dir, test_dir, design_dir))

    return HybridToolRegistry(events, real, real_policy, graceful=True)


def _build_rerun_llm():
    """构建真实 LLM（rerun 模式）。"""
    from app.agent_base.core.llm import BaseAgentsLLM
    return BaseAgentsLLM.from_settings(temperature=0.3)


async def replay_agent_session(
    session_id: str, *, mode: str = "mock", until_turn: int | None = None,
    tool_policy: str = "readonly",
) -> dict:
    """按 session 重放整段 agent 会话，逐轮返回 final_answer 与记录的对比。

    Args:
        mode: "mock"（默认，全 mock 零网络）/ "rerun"（真调 LLM，工具仍 mock）
            / "live"（真调 LLM + 只读工具真实执行，其余 mock）。
        until_turn: 只重放到第 N 轮（1-based，累积语义）。None 表示重放全部轮次。
            单步执行：复用同一个 agent 实例逐轮 arun，历史自然累积到第 N 轮，
            等价于全量重放的前 N 轮前缀（mock 模式下逐字一致；rerun 下省后续 token）。
        tool_policy: live 模式下真实执行的工具策略："readonly"（默认，read_file/
            glob 真实）或 "full"（write_file/edit_file/bash 也真实，有副作用风险）。

    返回：
        turns: [{user_message, final_answer, recorded_answer, matches, error, steps,
                recorded_steps}]
            steps 为回放侧步级执行明细；recorded_steps 为原始运行侧（从 trace 还原，
            仅在 rerun/live 模式与 steps 做左右对比有意义）。两者同构：
            [{step, thought, actions, tool_calls, is_final}]，
            tool_calls 每项 {name, arguments, observation}。
        executed_turns / total_turns: 已执行轮数 / 总轮数（until_turn 截断时不同）
        llm_calls / llm_total / tool_calls / tool_total / mode / all_matched
    """
    from app.trace.trace_reader import read_trace
    from app.agent_base.agents.react_agent import ReActAgent

    if mode not in ("mock", "rerun", "live"):
        raise ValueError(f"未知回放模式: {mode}（可选 mock / rerun / live）")

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

    total_turns = len(turns)
    if until_turn is not None:
        turns = turns[:until_turn]

    # 原始运行侧步级执行（与 turns 同源切分，供 rerun 左右对比）。
    # agent_step/tool_call/tool_result 只由原始运行写入，回放不写，天然是原始侧。
    turn_groups = _split_turn_events(events) or [events]
    recorded_steps_by_turn = [_extract_recorded_steps(g) for g in turn_groups]
    recorded_steps_by_turn = recorded_steps_by_turn[: len(turns)]

    llm = ReplayLLM(events) if mode == "mock" else _build_rerun_llm()

    # 重建原始上下文：真调 LLM（rerun/live）需与原始运行一致的 system prompt 与
    # workspace 上下文，否则轨迹大幅漂移。mock 模式 ReplayLLM 忽略 messages，
    # 传入亦无副作用。
    original_system_prompt, original_context = _reconstruct_original_context(
        events, turns[0]["message"] if turns else ""
    )

    # live 模式还原真实 workspace 目录，供真实工具（read_file/glob/...）定位文件。
    source_dir, test_dir, design_dir, _project_file = _reconstruct_workspace(events)

    if mode == "live":
        registry = _build_live_registry(
            events, source_dir, test_dir, design_dir, tool_policy=tool_policy,
        )
    else:
        registry = MockToolRegistry(events, graceful=(mode == "rerun"))

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
        system_prompt=original_system_prompt or None,
        max_steps=max(max_step, 5),
    )

    from app.trace.tracing import push_trace_hook, pop_trace_hook

    # 回放期间屏蔽 trace 写入：rerun 模式真调 LLM，若全局 trace 钩子仍指向
    # 某会话，会把回放自身的调用写进该会话 trace，污染后续回放。压入 no-op 隔离。
    push_trace_hook(_suppress_trace_hook)
    try:
        results = []
        for idx, t in enumerate(turns):
            steps: list[dict] = []
            try:
                answer = ""
                async for progress in agent.arun_stream(
                    t["message"], context=original_context
                ):
                    steps.append({
                        "step": progress.step,
                        "thought": progress.thought,
                        "actions": progress.actions,
                        "tool_calls": progress.tool_calls_detail,
                        "is_final": progress.is_final,
                    })
                    if progress.is_final:
                        answer = progress.final_answer
                if not answer:
                    # 达到 max_steps 仍未收敛：与 arun() 的兜底口径一致
                    answer = f"抱歉，在 {agent.max_steps} 步内未能完成任务。"
                error = None
            except ReplayExhausted as exc:
                answer = ""
                error = str(exc)
            recorded = t["recorded_answer"]
            recorded_steps = (
                recorded_steps_by_turn[idx] if idx < len(recorded_steps_by_turn) else []
            )
            results.append({
                "user_message": t["message"],
                "final_answer": answer,
                "recorded_answer": recorded,
                "matches": (not error and recorded is not None and answer == recorded),
                "error": error,
                "steps": steps,
                "recorded_steps": recorded_steps,
            })
    finally:
        pop_trace_hook(_suppress_trace_hook)

    llm_total = len(_step_level_events(events, "llm_response"))
    tool_total = len(_pick(events, "tool_result"))

    all_matched = all(r["matches"] for r in results)
    if mode == "mock" and until_turn is None:
        # mock 模式额外校验游标是否完整消费（回放流程与记录一致）。
        # 仅全量回放才要求消费完；until_turn 截断时游标天然消费不完，
        # 此时 all_matched 只看已执行轮次的 matches。
        all_matched = all_matched and llm.cursor == llm_total and registry.cursor == tool_total

    return {
        "session_id": session_id,
        "mode": mode,
        "turns": results,
        "executed_turns": len(results),
        "total_turns": total_turns,
        "llm_calls": getattr(llm, "cursor", None),
        "llm_total": llm_total,
        "tool_calls": registry.cursor,
        "tool_total": tool_total,
        "all_matched": all_matched,
    }
