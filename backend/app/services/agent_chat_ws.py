"""
Agent 对话 WebSocket 端点 — 前端对话框驱动开发的后端服务

架构：
    用户消息 → 意图分类 → chat 模式（轻量闲聊）或 dev 模式（ReActAgent + 工具）

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
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.tools.my_tools.conversation_tools import (
    create_conversation_tools, ProgressRelay,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent-chat"])

# ── 意图分类 prompt ────────────────────────────────────

INTENT_CLASSIFY_PROMPT = """你是一个消息分类器。判断用户消息属于哪种类型，只回复一个单词：

- **dev** — 用户想开发/创建/设计/修改软件系统、代码、UML图、架构。包括：
  创建项目、生成代码、设计类图/时序图、修复bug、写测试、
  优化代码、重构、实现功能、添加模块 等。

- **chat** — 其他一切：打招呼、闲聊、询问概念、问问题、
  讨论技术但不涉及具体代码开发 等。

只回复 "dev" 或 "chat"，不要任何解释。"""


# ── 意图分类 ─────────────────────────────────────────

async def _classify_intent(llm: BaseAgentsLLM, message: str) -> str:
    """使用 LLM 分类用户意图 — chat 或 dev。"""
    try:
        response = await llm.ainvoke([
            {"role": "system", "content": INTENT_CLASSIFY_PROMPT},
            {"role": "user", "content": message},
        ], temperature=0.0, max_tokens=4, model="deepseek-v4-flash")

        result = response.strip().lower()
        if "dev" in result:
            return "dev"
        return "chat"

    except Exception:
        logger.exception("[AgentChat] Intent classification failed, defaulting to chat")
        return "chat"


# ── 闲聊模式 — 直接 LLM 对话 ──────────────────────────

async def _handle_chat(
    llm: BaseAgentsLLM,
    message: str,
    history: list[dict],
    websocket: WebSocket,
    stop_check=None,
    source_dir: str = "",
    test_dir: str = "",
    project_file: str = "",
):
    """轻量闲聊 — 流式 LLM 调用，注入项目上下文但不加载工具。"""
    # 构建项目上下文
    context = _build_project_context(source_dir, test_dir, project_file)

    # ── 知识图谱补充：检索与用户问题相关的项目内容 ──
    kg_context = await _build_kg_chat_context(project_file, message)

    system_prompt = (
        "你是 AI 开发助手，一个友好的技术伙伴。"
        "你可以讨论编程、架构设计、技术选型、UML设计模式等话题。"
        "当用户想做具体开发任务（创建项目、生成代码、设计UML等）时，"
        "请告知他们你可以切换到开发模式来完成这些任务。"
        "用自然、有帮助的口吻回复，使用中文。"
        "\n\n"
        "## 当前项目信息\n"
        + context +
        "\n\n"
        "## 项目知识（来自知识图谱）\n"
        + kg_context
    )

    messages = [{"role": "system", "content": system_prompt}]
    # 加入最近的对话历史
    for h in history[-10:]:
        if h.get("role") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    # 流式输出
    full_response = ""
    try:
        async for chunk in llm.athink(messages, temperature=0.7):
            if stop_check and stop_check():
                await websocket.send_json({
                    "event": "stopped", "reason": "User requested stop",
                })
                return
            full_response += chunk
            await websocket.send_json({
                "event": "chat_chunk",
                "content": chunk,
            })
    except Exception as e:
        logger.exception("[AgentChat] Chat streaming error")
        await websocket.send_json({
            "event": "error", "message": f"Chat error: {e}",
        })
        return

    await websocket.send_json({
        "event": "done",
        "result": full_response,
    })


# ── 开发模式 — ReActAgent + 工具 ───────────────────────

def _build_project_context(
    source_dir: str,
    test_dir: str,
    project_file: str,
) -> str:
    """构建项目上下文摘要（文件路径信息，不包含文件内容）。"""
    lines: list[str] = []

    # ── 设计文件 ──
    if project_file and os.path.isfile(project_file):
        fname = os.path.basename(project_file)
        fsize = os.path.getsize(project_file)
        lines.append(f"- 设计文件: {project_file} ({fname}, {fsize} bytes)")
    elif project_file:
        lines.append(f"- 设计文件: {project_file} (未保存或路径无效)")
    else:
        lines.append("- 设计文件: 未保存")

    # ── 源码目录 ──
    if source_dir and os.path.isdir(source_dir):
        try:
            files = [f for f in os.listdir(source_dir) if os.path.isfile(os.path.join(source_dir, f))]
            py_files = [f for f in files if f.endswith('.py')]
            lines.append(f"- 源码目录: {source_dir} ({len(files)} 文件, {len(py_files)} 个 .py)")
            if py_files:
                sample = py_files[:20]
                lines.append(f"  源码文件: {', '.join(sample)}" + (f" ... 等{len(py_files)}个" if len(py_files) > 20 else ""))
        except OSError:
            lines.append(f"- 源码目录: {source_dir} (无法读取)")
    elif source_dir:
        lines.append(f"- 源码目录: {source_dir} (目录不存在)")
    else:
        lines.append("- 源码目录: 未设置（将从 UML 新生成代码）")

    # ── 测试目录 ──
    if test_dir and os.path.isdir(test_dir):
        try:
            files = [f for f in os.listdir(test_dir) if os.path.isfile(os.path.join(test_dir, f))]
            test_files = [f for f in files if f.startswith('test_') or f.endswith('_test.py')]
            lines.append(f"- 测试目录: {test_dir} ({len(files)} 文件, {len(test_files)} 个测试)")
            if test_files:
                sample = test_files[:20]
                lines.append(f"  测试文件: {', '.join(sample)}" + (f" ... 等{len(test_files)}个" if len(test_files) > 20 else ""))
        except OSError:
            lines.append(f"- 测试目录: {test_dir} (无法读取)")
    elif test_dir:
        lines.append(f"- 测试目录: {test_dir} (目录不存在)")
    else:
        lines.append("- 测试目录: 未设置（将自动生成 pytest 测试）")

    has_source = bool(source_dir and os.path.isdir(source_dir))
    has_test = bool(test_dir and os.path.isdir(test_dir))

    if has_source and has_test:
        lines.append("\n这是已有项目！优先增量修改而非全量覆盖。先检查已有代码和测试状态再决定策略。")
    elif has_source:
        lines.append("\n基于已有源码增量开发。先了解现有代码再修改。")
    elif has_test:
        lines.append("\n可能需要从测试反推代码实现（TDD）。")
    else:
        lines.append("\n全新项目：需求 → UML 设计 → 代码生成 → 验证 → 测试。")

    return "\n".join(lines)


async def _build_kg_chat_context(project_file: str, user_message: str) -> str:
    """从知识图谱检索与用户消息相关的项目内容，生成 chat 模式的上下文.

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
            # 无匹配结果，返回项目总体摘要
            db2 = KnowledgeGraphDB(kg_db)
            stats = db2.stats(project_id)
            db2.close()
            type_lines = "\n".join(f"  - {t}: {c} 个" for t, c in stats.get("by_type", {}).items())
            return (
                f"项目包含 {stats['total_nodes']} 个设计元素：\n{type_lines}\n\n"
                f"（未找到与你的问题直接相关的元素，但上述是项目的整体结构概况。）"
            )

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


def _create_dev_agent(
    llm: BaseAgentsLLM,
    source_dir: str = "",
    test_dir: str = "",
    project_file: str = "",
):
    """创建对话 Agent 实例，注册全部 12 个工具 (8 核心 + 4 知识图谱)。"""
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
        kg_tools = create_kg_tools(db_path=_kg_db, source_dir=source_dir, test_dir=test_dir)
        tools.extend(kg_tools)
        logger.info(f"[AgentChat] Registered {len(kg_tools)} KG tools (db={_kg_db})")
    except Exception:
        logger.exception("[AgentChat] Failed to register KG tools, continuing without them")

    registry = ToolRegistry()
    for t in tools:
        registry.register_tool(t)

    context = _build_project_context(source_dir, test_dir, project_file)

    agent = ReActAgent(
        name="DevAgent",
        llm=llm,
        tool_registry=registry,
        system_prompt=(
            "你是全栈 Python 开发专家。使用工具按需完成开发任务：\n"
            "## 开发工具\n"
            "1. optimize_uml — 从需求设计或优化 UML 图\n"
            "2. generate_code — 从 UML 生成 Python 代码\n"
            "3. validate_code — 验证代码（语法/导入/运行时）\n"
            "4. generate_tests — 生成 pytest 测试\n"
            "5. run_tests — 运行测试检查\n"
            "6. fix_code — 如果测试失败则修复源码\n"
            "7. write_files — 将最终代码保存到磁盘\n"
            "8. request_review — 在关键决策点请求人工审核\n\n"
            "## 知识图谱工具（了解项目结构和设计代码一致性）\n"
            "9.  kg_query  — 全文检索类、组件、方法、源码文件\n"
            "10. kg_expand — 展开节点关系（方法、属性、继承、依赖）\n"
            "11. kg_trace  — 追踪节点间依赖路径\n"
            "12. kg_diff   — 对比 UML 设计与源码实现差异\n\n"
            "## 知识图谱使用建议\n"
            "- 在开始修改代码前，先用 kg_query 了解项目中已有的类和组件结构\n"
            "- 修改类关系时，用 kg_expand 查看当前继承链和依赖关系，避免破坏\n"
            "- 代码生成完成后，用 kg_diff 检查 UML 设计是否全部实现\n"
            "- 如果 kg_query 摘要信息不够，用 kg_expand(node_ids=..., depth=2) 获取完整上下文\n\n"
            "## 项目上下文\n"
            + context + "\n\n"
            "注意：如果用户只是闲聊或问问题，直接以文本回复，"
            "不要调用任何工具。"
            "只有用户明确要开发/创建/生成/修改代码时才使用工具。"
        ),
        max_steps=12,
        use_native_fc=True,
    )
    return agent, review_mgr


async def _handle_dev(
    agent: ReActAgent,
    review_mgr,
    user_message: str,
    websocket: WebSocket,
    stop_check,
):
    """开发模式 — ReActAgent 流式执行，进度推送到前端。"""
    try:
        async for progress in agent.arun_stream(user_message):
            if stop_check():
                await websocket.send_json({
                    "event": "stopped", "reason": "User requested stop",
                })
                return

            d = progress.to_dict()

            # 检查是否有审核请求
            if d["tool_calls_detail"] and review_mgr and review_mgr.has_pending():
                pending = review_mgr.get_pending()
                for i, pr in enumerate(pending):
                    await websocket.send_json({
                        "event": "request_review",
                        "review_id": i,
                        "review_type": pr.get("review_type", "code"),
                        "title": pr.get("title", ""),
                        "content": pr.get("content", ""),
                        "question": pr.get("question", ""),
                        "step": d["step"],
                    })
                continue

            await websocket.send_json({
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

            if d["is_final"]:
                await websocket.send_json({
                    "event": "done",
                    "result": d["final_answer"],
                })
                return

    except Exception as e:
        logger.exception("[AgentChat] Dev agent execution error")
        await websocket.send_json({
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
    conversation_history: list[dict] = []  # [{role, content}, ...]
    current_mode: str = ""  # "chat" or "dev"

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

                # ── 意图分类 ──
                intent = await _classify_intent(llm, user_message)
                logger.info("[AgentChat] Intent: %s | message: %s", intent, user_message[:80])

                # 保存用户消息到历史
                conversation_history.append({"role": "user", "content": user_message})

                if intent == "chat":
                    # ── 闲聊模式 ──
                    current_mode = "chat"
                    await _handle_chat(
                        llm, user_message, conversation_history, websocket, _stop_check,
                        source_dir=source_dir, test_dir=test_dir, project_file=project_file,
                    )
                    conversation_history.append({
                        "role": "assistant",
                        "content": "(chat response, see UI)",
                    })

                else:
                    # ── 开发模式 ──
                    current_mode = "dev"
                    dev_agent, review_mgr = _create_dev_agent(llm, source_dir, test_dir, project_file)
                    await _handle_dev(
                        dev_agent, review_mgr, user_message, websocket, _stop_check,
                    )
                    conversation_history.append({
                        "role": "assistant",
                        "content": f"(dev task: {user_message[:100]})",
                    })

                # 裁剪历史
                if len(conversation_history) > 40:
                    conversation_history = conversation_history[-20:]

            # ── 停止对话 ──
            elif msg_type == "stop":
                stop_requested = True
                await websocket.send_json({"event": "stopped", "reason": "User requested stop"})

            # ── 人工审核回复 ──
            elif msg_type == "review_response":
                review_id = msg.get("review_id", 0)
                response = msg.get("response", "")
                if review_mgr:
                    review_mgr.resolve(review_id, response)
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
        try:
            await websocket.send_json({"event": "error", "message": f"Server error: {e}"})
        except Exception:
            pass
