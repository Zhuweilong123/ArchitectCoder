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

import json
import logging
import os
import threading
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.tools.my_tools.conversation_tools import (
    create_conversation_tools, ProgressRelay,
)
from app.services.chat_trace import ChatTraceLogger, set_trace_hook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent-chat"])


def _trace_hook_bridge(kind: str, *args, **kwargs):
    """全局 LLM trace hook 处理器 — 转发到当前会话的 ChatTraceLogger。

    由 llm.py 的 _trace_hook() 调用，签名: (kind, **kwargs)。
    kind: 'llm_request' | 'llm_response'
    """
    tracer = _TRACE_BRIDGE.get("tracer")
    if tracer is None:
        return None
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
            )
        elif kind == "llm_response":
            tracer.llm_response(
                span_id=kwargs.get("span_id", ""),
                content=kwargs.get("content", ""),
                tool_calls=kwargs.get("tool_calls"),
                usage=kwargs.get("usage"),
                error=kwargs.get("error", ""),
                duration_ms=kwargs.get("duration_ms", 0.0),
            )
            return None
    except Exception:
        logger.exception("[Trace] Bridge failed for kind=%s", kind)
    return None


_TRACE_BRIDGE: dict = {"tracer": None}


def _set_trace_bridge(tracer: ChatTraceLogger | None):
    _TRACE_BRIDGE["tracer"] = tracer


# ── 会话级交互日志 ───────────────────────────────────
# 每次 WebSocket 连接一个 markdown 文件，落盘到 temp/chat_log/，
# 记录用户消息、AI 回复、以及 dev 模式下每一步工具调用与返回结果。

def _chat_log_dir() -> str:
    """计算 chat_log 目录（与 pipeline_log 同级）。"""
    from app.core.config import get_settings as _get_settings
    _settings = _get_settings()
    return os.path.normpath(os.path.abspath(
        os.path.join(os.path.dirname(_settings.uml_dir), "chat_log"),
    ))


class ChatSessionLogger:
    """会话级交互日志器 — 每连接一个文件，事件即时追加写入。

    Usage:
        cl = ChatSessionLogger()
        cl.add_user("你好")
        cl.add_ai_chat("...", kg_context="...")
        cl.add_dev_step(progress)
        cl.add_done(answer)
        cl.close()
    """

    def __init__(self, session_id: str = ""):
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self._started = datetime.now()
        self._lock = threading.Lock()
        self._path: str | None = None
        self._closed = False

    @property
    def path(self) -> str:
        if self._path is None:
            log_dir = _chat_log_dir()
            os.makedirs(log_dir, exist_ok=True)
            fname = f"chat_{self.session_id}.md"
            self._path = os.path.join(log_dir, fname)
        return self._path

    def _ensure_header(self) -> None:
        """文件头仅写入一次。"""
        if os.path.exists(self.path):
            return
        header = (
            f"# AI 助手会话日志 — {self.session_id}\n\n"
            f"> 开始时间: {self._started.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"> 文件: {self.path}\n\n"
            "---\n\n"
        )
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(header)

    def _append(self, text: str) -> None:
        if self._closed:
            return
        try:
            self._ensure_header()
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(text)
        except Exception:
            logger.exception("[ChatLog] Failed to append to %s", self.path)

    # ── 记录方法 ─────────────────────────────────────

    def add_system(self, text: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._append(f"### [{ts}] {text}\n\n")

    def add_user(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._append(f"## 👤 用户 [{ts}]\n\n{message}\n\n")

    def add_chat_ctx(self, label: str, content: str) -> None:
        if not content:
            return
        self._append(f"> {label}:\n\n```text\n{content}\n```\n\n")

    def add_ai_chat(self, reply: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._append(f"## 🤖 AI (chat) [{ts}]\n\n{reply}\n\n---\n\n")

    def add_dev_step(self, progress) -> None:
        """记录 dev 模式单步：thought / actions / 工具调用与完整返回。"""
        ts = datetime.now().strftime("%H:%M:%S")
        d = progress.to_dict()
        block = [f"## 🛠️ Step {d['step']} [{ts}]\n"]
        if d["actions"]:
            block.append(f"- 工具: {', '.join(d['actions'])}\n")
        if progress.thought:
            block.append(f"- 思考: {progress.thought}\n")
        block.append("")
        for td in d["tool_calls_detail"]:
            block.append(f"**调用 {td.get('name', '?')}**\n")
            args = td.get("arguments", {})
            block.append(f"- 参数:\n```json\n{json.dumps(args, ensure_ascii=False, indent=2)}\n```\n")
            obs = td.get("observation", "")
            block.append(f"- 返回:\n```text\n{obs}\n```\n")
        block.append("---\n\n")
        self._append("\n".join(block))

    def add_review(self, review_type: str, title: str, question: str, content: str = "") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        block = [
            f"## 🔔 审核请求 [{ts}]\n",
            f"- 类型: {review_type}\n",
            f"- 标题: {title}\n",
            f"- 问题: {question}\n",
        ]
        if content:
            block.append(f"- 内容:\n```text\n{content}\n```\n")
        block.append("\n---\n\n")
        self._append("\n".join(block))

    def add_review_response(self, response: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._append(f"## ✅ 审核回复 [{ts}]\n\n{response}\n\n---\n\n")

    def add_done(self, answer: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._append(f"## 🏁 完成 [{ts}]\n\n{answer}\n\n---\n\n")

    def add_error(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._append(f"## ❌ 错误 [{ts}]\n\n{message}\n\n---\n\n")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self._ensure_header()
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(f"\n> 会话结束: {end}\n")
            logger.info("[ChatLog] Session logged → %s", self.path)
        except Exception:
            logger.exception("[ChatLog] Failed to finalize %s", self.path)

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
):
    """创建对话 Agent 实例，注册全部 12 个工具 (8 核心 + 4 知识图谱)。

    注入项目上下文与知识图谱结构摘要，使 agent 在纯问答/总结场景
    也能感知 UML 设计的真实内容（类、组件、接口、图）。
    """
    tools, review_mgr = create_conversation_tools(
        llm, source_dir=source_dir, test_dir=test_dir, include_review=True,
    )

    # ── 知识图谱工具 ──
    try:
        from app.agent_base.tools.my_tools.knowledge_graph_tools import create_kg_tools
        # KG DB 路径: 与 file_service 保持一致
        import os as _os
        from app.core.config import get_settings as _get_settings
        _settings = _get_settings()
        _kg_db = _os.path.normpath(_os.path.abspath(
            _os.path.join(_os.path.dirname(_settings.uml_dir), "data", "knowledge_graph.db"),
        ))
        kg_tools = create_kg_tools(
            db_path=_kg_db, source_dir=source_dir, test_dir=test_dir,
            project_file=project_file,
        )
        tools.extend(kg_tools)
        logger.info(f"[AgentChat] Registered {len(kg_tools)} KG tools (db={_kg_db})")
    except Exception:
        logger.exception("[AgentChat] Failed to register KG tools, continuing without them")

    registry = ToolRegistry()
    for t in tools:
        registry.register_tool(t)

    # ── 项目信息工具（按需获取，不再注入 prompt，首轮 token 更省、信息永远新鲜）──
    from app.agent_base.tools.my_tools.project_info_tools import ProjectInfoTool
    registry.register_tool(ProjectInfoTool(
        source_dir=source_dir, test_dir=test_dir, project_file=project_file,
    ))

    agent = ReActAgent(
        name="DevAgent",
        llm=llm,
        tool_registry=registry,
        system_prompt=(
            "你是 AI 开发助手，遵循以下原则：\n"
            "- 直接给答案，不重复用户的问题。\n"
            "- 涉及代码时先查看已有实现再修改，不凭空设计。\n"
            "- 回答简洁：先说结论或关键步骤，需要时再给代码。\n"
            "- 仅处理用户明确提出的需求，不预设未来场景、不做额外重构。\n"
            "- 代码不加注释、不用 emoji（除非用户明确要求）。"
        ),
        max_steps=12,
        use_native_fc=True,
    )
    return agent, review_mgr


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
    chat_log: ChatSessionLogger | None = None,
    trace_log: ChatTraceLogger | None = None,
):
    """ReActAgent 执行 — 单 agent 承接所有消息，进度推送到前端。

    该函数同时服务闲聊与开发：agent 依据 system prompt 自行决定
    是否调用工具（闲聊直接文本回复，开发调工具）。
    """
    try:
        async for progress in agent.arun_stream(user_message):
            if stop_check():
                await _ws_send(websocket, {
                    "event": "stopped", "reason": "User requested stop",
                })
                return

            d = progress.to_dict()

            # 检查是否有审核请求
            if d["tool_calls_detail"] and review_mgr and review_mgr.has_pending():
                pending = review_mgr.get_pending()
                for i, pr in enumerate(pending):
                    if chat_log:
                        chat_log.add_review(
                            pr.get("review_type", "code"),
                            pr.get("title", ""),
                            pr.get("question", ""),
                            pr.get("content", ""),
                        )
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
            if chat_log:
                chat_log.add_dev_step(progress)
            if trace_log:
                trace_log.agent_step(
                    step=d["step"], thought=progress.thought or "",
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
                        "observation": str(td.get("observation", ""))[:500],
                    }
                    for td in d.get("tool_calls_detail", [])[:5]
                ],
                "is_final": d["is_final"],
                "final_answer": d["final_answer"] if d["is_final"] else "",
            })
            if not ok:
                return

            if d["is_final"]:
                # 跨轮记忆：本轮 user + assistant 一起写入 agent 历史
                try:
                    from app.agent_base.core.message import Message
                    agent.add_message(Message(content=user_message, role="user"))
                    if d["final_answer"]:
                        agent.add_message(Message(content=d["final_answer"], role="assistant"))
                except Exception:
                    logger.exception("[AgentChat] Failed to append messages to agent history")
                if chat_log:
                    chat_log.add_done(d["final_answer"])
                if trace_log:
                    trace_log.done(answer=d["final_answer"])
                ok = await _ws_send(websocket, {
                    "event": "done",
                    "result": d["final_answer"],
                })
                if not ok:
                    return
                return

    except Exception as e:
        logger.exception("[AgentChat] Dev agent execution error")
        if chat_log:
            chat_log.add_error(f"Agent error: {e}")
        if trace_log:
            trace_log.error(event_type="agent", message=f"Agent error: {e}")
        await _ws_send(websocket, {
            "event": "error", "message": f"Agent error: {e}",
        })


# ── WebSocket 端点 ──────────────────────────────────────

@router.websocket("/ws/chat")
async def agent_chat_ws(websocket: WebSocket):
    """Agent 对话 WebSocket — 流式双向通信。"""
    await websocket.accept()
    logger.info("[AgentChat] WebSocket connected")

    llm: BaseAgentsLLM | None = None
    dev_agent: ReActAgent | None = None
    review_mgr = None
    stop_requested = False
    source_dir = ""
    test_dir = ""
    project_file = ""
    chat_log = ChatSessionLogger()
    trace_log = ChatTraceLogger(session_id=chat_log.session_id)
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

                # 记录用户消息（markdown + trace）
                chat_log.add_user(user_message)
                trace_log.user_message(user_message, project_file=project_file)

                # ── 单 agent 承接所有消息：懒创建 + 跨轮复用 ──
                if dev_agent is None:
                    dev_agent, review_mgr = await _create_dev_agent(
                        llm, source_dir, test_dir, project_file,
                    )

                await _handle_dev(
                    dev_agent, review_mgr, user_message, websocket, _stop_check,
                    chat_log=chat_log, trace_log=trace_log,
                )

            # ── 停止对话 ──
            elif msg_type == "stop":
                stop_requested = True
                chat_log.add_system("用户请求停止")
                trace_log.error(event_type="user_stop", message="用户请求停止")
                await websocket.send_json({"event": "stopped", "reason": "User requested stop"})

            # ── 人工审核回复 ──
            elif msg_type == "review_response":
                review_id = msg.get("review_id", 0)
                response = msg.get("response", "")
                if review_mgr:
                    review_mgr.resolve(review_id, response)
                    chat_log.add_review_response(response)
                    trace_log.review_response(review_id=review_id, response=response)
                    logger.info("[AgentChat] Review %d resolved: %s", review_id, response[:80])

            else:
                await websocket.send_json({
                    "event": "error", "message": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        stop_requested = True
        logger.info("[AgentChat] WebSocket disconnected")
    except Exception as e:
        logger.exception("[AgentChat] Unexpected error")
        chat_log.add_error(f"Server error: {e}")
        trace_log.error(event_type="server", message=f"Server error: {e}")
        try:
            await websocket.send_json({"event": "error", "message": f"Server error: {e}"})
        except Exception:
            pass
    finally:
        chat_log.close()
        trace_log.close()
        set_trace_hook(None)
        _set_trace_bridge(None)
