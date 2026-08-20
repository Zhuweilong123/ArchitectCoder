"""
Agent 对话 WebSocket 端点 — 前端对话框驱动开发的后端服务

架构：
    用户消息 → 单 ReActAgent（依据 system prompt 自行决定聊天回复或调用工具）

WebSocket 协议:
    客户端 → 服务端: JSON
        {"type": "chat", "message": "创建一个计算器系统"}
        {"type": "stop"}                          # 中断当前 Agent
        {"type": "review_response", "review_id": 0, "response": "批准"}  # 人工审核回复

    服务端 → 客户端: JSON (stream)
        {"event": "progress", "step": 1, "actions": [...], "tool_calls_detail": [...]}
        {"event": "request_review", "review_id": 0, "review_type": "code", "title": "...", "question": "..."}
        {"event": "done", "result": "..."}
        {"event": "stopped", "reason": "..."}
        {"event": "error", "message": "..."}
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.core.hooks import AgentRuntime, set_runtime, reset_runtime
from app.agent_base.core.exceptions import AgentInterrupted
from app.agent_base.tools.my_tools.conversation_tools import (
    create_conversation_tools, ProgressRelay,
)
from app.services.chat_trace import ChatTraceLogger, set_trace_hook
from app.services.trace_reader import reconstruct_history
from app.services.agent_session import get_or_create

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent-chat"])


def _trace_hook_bridge(kind: str, *args, **kwargs):
    """全局 LLM trace hook 处理器 — 转发到当前会话的 ChatTraceLogger。

    由 llm.py 的 _trace_hook() 调用，签名: (kind, **kwargs)。
    kind: 'llm_request' | 'llm_response'
    """
    from app.services.chat_trace import current_trace_spans

    tracer = _TRACE_BRIDGE.get("tracer")
    if tracer is None:
        return None
    spans = current_trace_spans()
    span_path = "/".join(spans) if spans else ""
    try:
        if kind == "llm_request":
            return tracer.llm_request(
                provider=kwargs.get("provider", "unknown"),
                model=kwargs.get("model", ""),
                messages=kwargs.get("messages", []),
                temperature=kwargs.get("temperature"),
                max_tokens=kwargs.get("max_tokens"),
                tools=kwargs.get("tools"),
                tool_choice=kwargs.get("tool_choice"),
                response_format=kwargs.get("response_format"),
                timeout=kwargs.get("timeout"),
                span_path=span_path,
            )
        elif kind == "llm_response":
            tracer.llm_response(
                span_id=kwargs.get("span_id", ""),
                content=kwargs.get("content", ""),
                tool_calls=kwargs.get("tool_calls"),
                usage=kwargs.get("usage"),
                error=kwargs.get("error", ""),
                duration_ms=kwargs.get("duration_ms", 0.0),
                span_path=span_path,
            )
            return None
    except Exception:
        logger.exception("[Trace] Bridge failed for kind=%s", kind)
    return None


_TRACE_BRIDGE: dict = {"tracer": None}


def _set_trace_bridge(tracer: ChatTraceLogger | None):
    _TRACE_BRIDGE["tracer"] = tracer


# ── 对话 Agent — ReActAgent + 工具 ──────────────────────

async def _build_kg_chat_context(project_file: str, user_message: str) -> str:
    """从知识图谱检索与用户消息相关的项目内容，生成对话上下文.

    Args:
        project_file: .umlproj 文件路径
        user_message: 用户当前消息

    Returns:
        格式化的知识图谱摘要文本，失败时返回空字符串。
    """
    if not project_file or not os.path.isfile(project_file):
        return "（未打开项目文件，无法获取设计内容。请先打开 .umlproj 项目文件。）"

    project_id = os.path.splitext(os.path.basename(project_file))[0]

    try:
        from knowledge_graph.retriever import GraphRetriever
        from knowledge_graph.builder import GraphBuilder
        from knowledge_graph.database import KnowledgeGraphDB
        from app.core.config import get_settings as _get_settings

        _settings = _get_settings()
        kg_db = os.path.normpath(os.path.abspath(
            os.path.join(os.path.dirname(_settings.uml_dir), "data", "knowledge_graph.db"),
        ))

        # 检查 KG 是否存在且包含该项目数据
        db = KnowledgeGraphDB(kg_db)
        existing = db.find_nodes(project_id, limit=1)
        if not existing:
            # 尝试从项目文件立即构建
            try:
                from app.services.file_service import load_project
                project = load_project(project_file)
                builder = GraphBuilder(db_path=kg_db)
                stats = builder.build_from_project(project, project_id)
                logger.info(f"[KG Chat] On-demand build: {stats}")
                builder.close()
            except Exception:
                db.close()
                return "（知识图谱尚未构建此项目的索引。请先保存项目文件以触发自动构建。）"

        db.close()

        # 检索与用户消息相关的内容
        retriever = GraphRetriever(db_path=kg_db)
        results = await retriever.query(
            project_id=project_id,
            pattern=user_message,
            top_k=15,
        )
        retriever.close()

        if not results:
            # 无匹配结果（泛化问句/中文分词失配），回退列出项目的具体设计元素
            db2 = KnowledgeGraphDB(kg_db)
            try:
                stats = db2.stats(project_id)
                # 拉取各类型的具体节点名，避免只给计数
                summary_types = ["class", "component", "interface", "diagram", "lifeline", "source_file", "test_file"]
                by_type_nodes: dict[str, list[str]] = {}
                for nt in summary_types:
                    nodes = db2.find_nodes(project_id, node_type=nt, limit=30)
                    by_type_nodes[nt] = [n.name for n in nodes]
            finally:
                db2.close()

            lines = [f"项目共 {stats['total_nodes']} 个设计元素（全文检索未命中你的问题，列出主要结构）：\n"]
            type_names = {
                "class": "类", "component": "组件", "interface": "接口", "diagram": "图",
                "lifeline": "生命线", "source_file": "源文件", "test_file": "测试文件",
            }
            for nt in summary_types:
                names = by_type_nodes.get(nt, [])
                if not names:
                    continue
                label = type_names.get(nt, nt)
                lines.append(f"### {label} ({len(names)})")
                lines.append("  " + ", ".join(names[:20]) + (" ..." if len(names) > 20 else ""))
                lines.append("")
            if not any(by_type_nodes.values()):
                lines.append("（该项目的知识图谱尚未索引具体设计内容，请先保存项目文件以触发索引。）")
            return "\n".join(lines)

        # 分组格式化
        by_type: dict[str, list] = {}
        for r in results:
            t = r.node.node_type.value
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(r)

        lines = [f"从知识图谱中找到 {len(results)} 个相关元素：\n"]
        type_names = {
            "project": "项目", "diagram": "图", "class": "类", "component": "组件",
            "lifeline": "生命线", "source_file": "源文件", "test_file": "测试文件",
            "method": "方法", "attribute": "属性", "interface": "接口",
        }
        for t, items in sorted(by_type.items()):
            label = type_names.get(t, t)
            lines.append(f"### {label}")
            for r in items[:8]:  # 每种类型最多 8 条
                props = r.node.properties
                extra = ""
                if t == "class":
                    stereo = props.get("stereotype", "")
                    methods = props.get("methods", [])
                    if stereo and stereo != "class":
                        extra = f" «{stereo}»"
                    if methods:
                        mn = [m.get("name", str(m)) for m in methods[:5]]
                        extra += f" | 方法: {', '.join(mn)}"
                elif t == "component":
                    ifaces = props.get("provided_interfaces", [])
                    if ifaces:
                        extra = f" | 提供: {', '.join(ifaces[:5])}"
                elif t == "method":
                    extra = f"({props.get('params', '')}): {props.get('return_type', '')}"
                elif t == "source_file":
                    extra = f" ({props.get('path', '')})"
                lines.append(f"  - **{r.node.name}**{extra}")
            lines.append("")

        return "\n".join(lines)

    except Exception:
        logger.exception("[AgentChat] Failed to build KG chat context")
        return ""


async def _create_dev_agent(
    llm: BaseAgentsLLM,
    source_dir: str = "",
    test_dir: str = "",
    project_file: str = "",
    user_message: str = "",
    progress: ProgressRelay | None = None,
    restore_history: list[dict] | None = None,
):
    """创建对话 Agent 实例，注册全部 12 个工具 (8 核心 + 4 知识图谱)。

    注入项目上下文与知识图谱结构摘要，使 agent 在纯问答/总结场景
    也能感知 UML 设计的真实内容（类、组件、接口、图）。
    """
    tools, review_mgr = create_conversation_tools(
        llm, source_dir=source_dir, test_dir=test_dir, project_file=project_file,
        include_review=True, progress=progress,
    )

    registry = ToolRegistry()
    for t in tools:
        registry.register_tool(t)

    base_prompt_parts = [
        "You are an AI development assistant. Follow these principles:",
        "- Give direct answers; do not restate the user's question.",
        "- When code is involved, examine existing implementations before modifying; "
        "do not design from scratch.",
        "- Be concise: lead with conclusions or key steps, provide code when needed.",
        "- Handle only what the user explicitly asked for; do not anticipate future "
        "scenarios or do extra refactoring.",
        "- Do not add comments or use emojis in code (unless explicitly requested).",
        "- If the user has not asked you to do anything (e.g. only greeting, thanking, "
        "commenting, or chatting), reply briefly without calling any tools.",
        "- For summarizing or overviewing the project's design/code/tests, call "
        "explore_project instead of reading many files yourself.",
    ]
    # ── 注入项目上下文 ──
    if project_file:
        base_prompt_parts.append(
            f"\n## Project Context\n"
            f"- Current project file: {project_file}\n"
            f"  (use this exact path as project_file parameter for optimize_uml; "
            f"do NOT guess or shorten the filename)"
        )
    # ── 注入 workspace 目录（非空才写，帮助 agent 直接定位，减少试探）──
    from app.core.config import get_settings
    # 设计目录：优先 project_file 所在目录（当前项目的 design_dir），否则全局 uml_dir
    design_dir = (os.path.dirname(os.path.abspath(project_file))
                  if project_file else os.path.abspath(get_settings().uml_dir))
    workspace_entries = [
        ("Source directory", source_dir),
        ("Test directory", test_dir),
        ("Design directory", design_dir),
    ]
    provided = [(label, d) for label, d in workspace_entries if d]
    if provided:
        lines = ["\n## Workspace (Windows environment, absolute paths)"]
        for label, d in provided:
            lines.append(f"- {label}: {d}")
        base_prompt_parts.append("\n".join(lines))
    base_prompt = "\n".join(base_prompt_parts)
    # 注入项目历史记忆（跨任务 recall）
    project_id = os.path.splitext(os.path.basename(project_file))[0] if project_file else ""
    system_prompt = await _build_memory_system_prompt(base_prompt, project_id, user_message)

    agent = ReActAgent(
        name="DevAgent",
        llm=llm,
        tool_registry=registry,
        system_prompt=system_prompt,
        max_steps=get_settings().agent_max_steps,
        use_native_fc=True,
    )
    if restore_history:
        agent.restore_history(restore_history)
    return agent, review_mgr


# ── 记忆系统（跨任务归档 + 注入） ───────────────────────────
# 复用 memory_system.MemoryManager：任务结束 (done) 后异步归档工具过程 + 结论，
# 新任务开始时 recall 相关记忆注入 system prompt。

def _memory_db_path() -> str:
    """记忆数据库路径：与 knowledge_graph.db 同目录。"""
    try:
        from app.core.config import get_settings as _get
        _settings = _get()
        return os.path.normpath(os.path.abspath(
            os.path.join(os.path.dirname(_settings.uml_dir), "data", "memories.db"),
        ))
    except Exception:
        return os.path.normpath(os.path.abspath(
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "memories.db"),
        ))


def _tool_steps_summary(tool_calls_detail: list[dict], max_steps: int = 8) -> str:
    """把一次任务的工具调用序列整理成结构化摘要文本（喂给记忆提取）。"""
    lines: list[str] = []
    for td in (tool_calls_detail or [])[:max_steps]:
        name = td.get("name", "?")
        args = td.get("arguments", {})
        obs = str(td.get("observation", ""))[:300]
        args_str = json.dumps(args, ensure_ascii=False)[:150] if isinstance(args, dict) else str(args)[:150]
        lines.append(f"[{name}] 参数:{args_str}\n返回:{obs}")
    return "\n".join(lines)


def _extract_fn_for(llm: BaseAgentsLLM):
    """构造 MemoryManager.remember 的 extract_fn：用当前 LLM 提取记忆。"""
    async def _extract(prompt: str) -> str:
        return await llm.ainvoke([{"role": "user", "content": prompt}])
    return _extract


async def _archive_task_to_memory(
    project_id: str,
    user_message: str,
    final_answer: str,
    tool_calls_detail: list[dict],
    llm: BaseAgentsLLM,
) -> None:
    """任务结束后异步归档：工具过程摘要 + 结论 → 记忆系统。

    在后台任务中调用（不阻塞主对话返回）。失败不影响主流程。
    """
    try:
        from memory_system.manager import MemoryManager
        mgr = MemoryManager(db_path=_memory_db_path())

        # 方案 B：把工具过程组装进 llm_output，让提取器捕捉关键事实
        steps_text = _tool_steps_summary(tool_calls_detail)
        combined = f"## 工具执行过程\n{steps_text}\n\n## 最终结论\n{final_answer}"
        await mgr.remember(
            project_id=project_id,
            context=f"对话 Agent 任务: {user_message[:100]}",
            llm_call_type="agent_task",
            user_input=user_message,
            llm_output=combined[:2000],
            extract_fn=_extract_fn_for(llm),
        )
        logger.info("[Memory] Archived task to memory (project=%s)", project_id)
    except Exception:
        logger.warning("[Memory] Archive to memory failed (non-fatal)", exc_info=True)


async def _build_memory_system_prompt(system_prompt: str, project_id: str, user_message: str) -> str:
    """recall 项目相关记忆并注入 system prompt（失败则原样返回）。"""
    if not project_id:
        return system_prompt
    try:
        from memory_system.manager import MemoryManager
        mgr = MemoryManager(db_path=_memory_db_path())
        results = await mgr.recall(project_id, user_message, top_k=5)
        return mgr.inject_memories(system_prompt, results)
    except Exception:
        logger.warning("[Memory] Inject memory failed (non-fatal)", exc_info=True)
        return system_prompt


async def _ws_send(websocket: WebSocket, payload: dict) -> bool:
    """发送 WebSocket 消息，连接已断开时返回 False 而非抛异常。

    前端可能在任何时刻断开（刷新/关闭面板），若继续在原连接上 send_json，
    会抛 "Unexpected ASGI message 'websocket.send', after sending 'websocket.close'"
    并可能击穿 uvicorn 进程。这里把发送失败转化为返回值，让调用方优雅终止 agent 循环。
    """
    try:
        await websocket.send_json(payload)
        return True
    except WebSocketDisconnect:
        logger.info("[AgentChat] WebSocket disconnected during send")
        return False
    except Exception:
        logger.warning("[AgentChat] WebSocket send failed (client likely closed)", exc_info=True)
        return False


async def _handle_dev(
    agent: ReActAgent,
    review_mgr,
    user_message: str,
    websocket: WebSocket,
    stop_check,
    trace_log: ChatTraceLogger | None = None,
    project_file: str = "",
    progress: ProgressRelay | None = None,
):
    """ReActAgent 执行 — 单 agent 承接所有消息，进度推送到前端。

    该函数同时服务闲聊与开发：agent 依据 system prompt 自行决定
    是否调用工具（闲聊直接文本回复，开发调工具）。

    progress (ProgressRelay): 若提供，则将其 design_element 事件转发
    到 WebSocket 供前端实时渲染（流式优化模式）。
    """

    async def _on_design_element(ev: dict):
        """将 ProgressRelay 的 design_element 事件转发为 WebSocket 消息"""
        if ev.get("event") == "design_element":
            await _ws_send(websocket, {
                "event": "design_element",
                "type": ev.get("type", ""),
                "data": ev.get("data", ""),
            })

    if progress:
        progress.on_progress(_on_design_element)
    _runtime_token = set_runtime(AgentRuntime(stop_check=stop_check))
    try:
        task_tool_calls: list[dict] = []  # 累计本任务所有工具调用（供记忆归档）
        async for step_progress in agent.arun_stream(user_message):
            d = step_progress.to_dict()
            task_tool_calls.extend(d.get("tool_calls_detail", []))

            # 检查是否有审核请求
            if d["tool_calls_detail"] and review_mgr and review_mgr.has_pending():
                pending = review_mgr.get_pending()
                for i, pr in enumerate(pending):
                    if trace_log:
                        trace_log.review_request(
                            review_id=i,
                            review_type=pr.get("review_type", "code"),
                            title=pr.get("title", ""),
                            question=pr.get("question", ""),
                            content=pr.get("content", ""),
                        )
                    ok = await _ws_send(websocket, {
                        "event": "request_review",
                        "review_id": i,
                        "review_type": pr.get("review_type", "code"),
                        "title": pr.get("title", ""),
                        "content": pr.get("content", ""),
                        "question": pr.get("question", ""),
                        "step": d["step"],
                    })
                    if not ok:
                        return
                continue

            # 记录完整工具调用与返回（在截断发给前端之前）
            if trace_log:
                trace_log.agent_step(
                    step=d["step"], thought=step_progress.thought or "",
                    actions=d["actions"], is_final=d["is_final"],
                )
                for td in d.get("tool_calls_detail", []):
                    tool_span = trace_log.tool_call(
                        step=d["step"],
                        tool_name=td.get("name", ""),
                        arguments=td.get("arguments", {}),
                    )
                    trace_log.tool_result(
                        span_id=tool_span,
                        tool_name=td.get("name", ""),
                        observation=str(td.get("observation", "")),
                        fed_truncated=bool(td.get("fed_truncated", False)),
                        fed_length=int(td.get("fed_length") or 0),
                    )

            ok = await _ws_send(websocket, {
                "event": "progress",
                "step": d["step"],
                "actions": d["actions"],
                "thought": d["thought"][:300],
                "tool_calls_detail": [
                    {
                        "name": td.get("name", ""),
                        "arguments": td.get("arguments", {}),
                        "observation": str(td.get("observation", ""))[:3000],
                    }
                    for td in d.get("tool_calls_detail", [])[:5]
                ],
                "is_final": d["is_final"],
                "final_answer": d["final_answer"] if d["is_final"] else "",
            })
            if not ok:
                return

            # 若本轮 optimize_uml 返回了更新后的设计，推送 design_updated 供前端 DiffViewer 审核
            for td in d.get("tool_calls_detail", []):
                if td.get("name") == "optimize_uml":
                    obs_str = str(td.get("observation", ""))
                    try:
                        obs = json.loads(obs_str)
                    except (TypeError, json.JSONDecodeError):
                        obs = None
                    if isinstance(obs, dict) and obs.get("diagrams"):
                        saved_to = obs.get("saved_to", "")
                        await _ws_send(websocket, {
                            "event": "design_updated",
                            "diagrams": obs.get("diagrams", []),
                            "saved_to": saved_to,
                            "review": True,  # 始终需要用户审核确认
                        })
                    break

            if d["is_final"]:
                # 历史由 _arun_with_fc_stream 内部统一写入，此处不再重复 add_message
                if trace_log:
                    trace_log.done(answer=d["final_answer"])

                # 异步后台归档到记忆系统（不阻塞返回 done）
                project_id = os.path.splitext(os.path.basename(project_file))[0] if project_file else ""
                if project_id and task_tool_calls:
                    asyncio.create_task(_archive_task_to_memory(
                        project_id=project_id,
                        user_message=user_message,
                        final_answer=d["final_answer"] or "",
                        tool_calls_detail=task_tool_calls,
                        llm=getattr(agent, "llm", None),
                    ))

                ok = await _ws_send(websocket, {
                    "event": "done",
                    "result": d["final_answer"],
                })
                if not ok:
                    return
                return

    except AgentInterrupted:
        await _ws_send(websocket, {
            "event": "stopped", "reason": "User requested stop",
        })
    except Exception as e:
        logger.exception("[AgentChat] Dev agent execution error")
        if trace_log:
            trace_log.error(event_type="agent", message=f"Agent error: {type(e).__name__}: {e}")
        await _ws_send(websocket, {
            "event": "error", "message": f"Agent error: {type(e).__name__}: {e}",
        })
    finally:
        reset_runtime(_runtime_token)


# ── WebSocket 端点 ──────────────────────────────────────

@router.websocket("/ws/chat")
async def agent_chat_ws(websocket: WebSocket):
    """Agent 对话 WebSocket — 流式双向通信。"""
    await websocket.accept()
    logger.info("[AgentChat] WebSocket connected")

    # 会话 id 来自前端（localStorage 持久化），跨连接复用 agent 历史与日志文件；
    # 旧前端未传时退化为按时间戳生成（等价于每次连接一个新会话）。
    session_id = websocket.query_params.get("session_id") or \
        datetime.now().strftime("%Y%m%d_%H%M%S")
    session = get_or_create(session_id)
    # 恢复历史会话：全新会话但磁盘上已有 trace → 重建对话历史，等 agent 创建时注入
    restore_history = None
    if session.agent is None:
        restore_history = reconstruct_history(session_id)
    if session.trace_log is None:
        trace_log = ChatTraceLogger(session_id=session_id)
        trace_log.start()  # 首次连接时写入会话开始边界（session_end 由 TTL 回收时 close 写入）
    else:
        trace_log = session.trace_log
    session.trace_log = trace_log

    llm: BaseAgentsLLM | None = None
    dev_agent: ReActAgent | None = session.agent
    review_mgr = session.review_mgr
    progress: ProgressRelay | None = session.progress
    stop_requested = False
    source_dir = ""
    test_dir = ""
    project_file = ""
    _set_trace_bridge(trace_log)
    set_trace_hook(_trace_hook_bridge)

    def _stop_check():
        return stop_requested

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"event": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "")

            # ── 开始对话 ──
            if msg_type == "chat":
                user_message = msg.get("message", "")
                source_dir = msg.get("source_dir", source_dir)
                test_dir = msg.get("test_dir", test_dir)
                project_file = msg.get("project_file", project_file)

                if not user_message:
                    await websocket.send_json({"event": "error", "message": "Empty message"})
                    continue

                if llm is None:
                    llm = BaseAgentsLLM.from_settings(temperature=0.3)

                stop_requested = False

                # 记录用户消息（trace）
                trace_log.user_message(user_message, project_file=project_file)

                # ── 单 agent 承接所有消息：懒创建 + 跨轮复用 ──
                if dev_agent is None:
                    progress = ProgressRelay()
                    dev_agent, review_mgr = await _create_dev_agent(
                        llm, source_dir, test_dir, project_file, user_message,
                        progress=progress, restore_history=restore_history,
                    )
                    session.agent, session.review_mgr, session.progress = \
                        dev_agent, review_mgr, progress

                session.touch()

                await _handle_dev(
                    dev_agent, review_mgr, user_message, websocket, _stop_check,
                    trace_log=trace_log, project_file=project_file,
                    progress=progress,
                )

            # ── 停止对话 ──
            elif msg_type == "stop":
                stop_requested = True
                trace_log.error(event_type="user_stop", message="用户请求停止")
                await websocket.send_json({"event": "stopped", "reason": "User requested stop"})

            # ── 人工审核回复 ──
            elif msg_type == "review_response":
                review_id = msg.get("review_id", 0)
                response = msg.get("response", "")
                if review_mgr:
                    review_mgr.resolve(review_id, response)
                    trace_log.review_response(review_id=review_id, response=response)
                    logger.info("[AgentChat] Review %d resolved: %s", review_id, response[:80])

            else:
                await websocket.send_json({
                    "event": "error", "message": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        stop_requested = True
        logger.info("[AgentChat] WebSocket disconnected")
    except RuntimeError as e:
        # 前端断开时 Starlette 会在 receive_text()/send_json() 抛这个错误；
        # 识别为正常断开，优雅收尾，不当作服务端错误处理。
        if "WebSocket is not connected" in str(e) or "not connected" in str(e):
            stop_requested = True
            logger.info("[AgentChat] WebSocket closed (client disconnected)")
        else:
            logger.exception("[AgentChat] Unexpected error")
            trace_log.error(event_type="server", message=f"Server error: {e}")
    except Exception as e:
        logger.exception("[AgentChat] Unexpected error")
        trace_log.error(event_type="server", message=f"Server error: {e}")
        try:
            await websocket.send_json({"event": "error", "message": f"Server error: {e}"})
        except Exception:
            pass
    finally:
        # 日志器不在此 close — 由 AgentSession 回收时统一 finalize，
        # 从而同一会话跨连接持续追加到同一 trace_*.jsonl。
        session.touch()
        set_trace_hook(None)
        _set_trace_bridge(None)
