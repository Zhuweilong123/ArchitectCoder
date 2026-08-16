"""项目探索子代理工具 — 按资源类型动态生成子 Agent 探索项目。

主对话 Agent 收到"总结/概览项目"类任务时，调用 explore_project。
内部按资源存在性（uml/source/test）动态探索，返回压缩结论。

设计要点:
- uml: 确定性流程 — 直接调 kg_project_structure 拿完整结构 → 一次 LLM 总结。
  不走 ReAct 循环，避免子代理"不信结构、重复查询"的泥潭。
- source/test: 列文件 → 读关键文件 → LLM 总结（同样确定性，不自主探索）。
- 每次探索后归档到项目记忆，下次检索注入。
- what='all' 时并行探索存在的资源（通常 1-2 个）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.my_tools.conversation_tools import AsyncTool

logger = logging.getLogger(__name__)

RESOURCE_LABELS = {
    "uml": "UML 设计",
    "source": "源代码",
    "test": "测试代码",
}


def _memory_db_path() -> str:
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


def _kg_db_path() -> str:
    try:
        from app.core.config import get_settings as _get
        _settings = _get()
        return os.path.normpath(os.path.abspath(
            os.path.join(os.path.dirname(_settings.uml_dir), "data", "knowledge_graph.db"),
        ))
    except Exception:
        return os.path.normpath(os.path.abspath(
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "knowledge_graph.db"),
        ))


# ═══════════════════════════════════════════════════════════════
# 确定性探索流程（不走 ReAct，避免子代理重复验证）
# ═══════════════════════════════════════════════════════════════

def _locate_in_file(project_file: str, target: str, context: int = 3) -> list[dict]:
    """在 .umlproj 里按关键词定位目标，返回结构化位置。

    target 可以是单词或短语（如 'loop', 'loop fragment', 类名）。多词短语
    拆成单词用 | 正则匹配，避免空格字面匹配失败。返回 [{line, offset, snippet}]。
    """
    import re
    if not project_file or not os.path.isfile(project_file):
        return []
    # 拆词：去掉标点，取每个单词，构造 "word1|word2|..." 正则
    words = [w for w in re.split(r'[\s,.;:]+', target) if w]
    if not words:
        return []
    pattern = "|".join(re.escape(w) for w in words)
    try:
        matcher = re.compile(pattern, re.IGNORECASE)
    except re.error:
        matcher = None

    hits: list[dict] = []
    with open(project_file, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    offset = 0
    for i, line in enumerate(lines):
        if matcher and matcher.search(line):
            hits.append({
                "line": i + 1,
                "offset": offset,
                "snippet": line.strip()[:200],
                "context": [l.strip()[:120] for l in lines[max(0, i - context):i + context + 1]],
            })
        offset += len(line)
    return hits[:10]


async def _locate_uml(llm: BaseAgentsLLM, project_id: str, project_file: str, target: str) -> str:
    """uml 定位：在 .umlproj 里按目标关键词定位元素，返回位置 + 结构化信息。"""
    # 先尝试从 KG 结构拿节点（如类/组件/方法），再回退文件定位
    located: list[dict] = []
    try:
        from app.agent_base.tools.my_tools.knowledge_graph_tools import KgProjectStructureTool
        struct_tool = KgProjectStructureTool(db_path=_kg_db_path(), project_file=project_file)
        raw = await struct_tool._execute({"project_id": project_id, "depth": 2})
        structure = json.loads(raw)
        # 在结构中找匹配 target 的节点
        for d in structure.get("diagrams", []):
            for c in d.get("children", []):
                if target.lower() in c.get("name", "").lower():
                    located.append({
                        "type": "node", "diagram": d.get("name"),
                        "name": c.get("name"), "id": c.get("id"),
                        "file_offset": d.get("file_offset"),
                    })
    except Exception:
        pass

    # 文件定位（精确行号 + 上下文）
    file_hits = _locate_in_file(project_file, target)
    if file_hits:
        for h in file_hits:
            located.append({
                "type": "file", "line": h["line"], "offset": h["offset"],
                "snippet": h["snippet"],
            })

    if not located:
        return f"（未在项目文件中定位到 '{target}'）"

    # 汇总为 JSON 返回，主 Agent 直接喂给 optimize_uml
    return json.dumps({"target": target, "matches": located[:10]}, ensure_ascii=False)


def _project_file_signature(project_file: str) -> dict | None:
    """读取 .umlproj 的图签名，用于交叉校验 KG 结构是否陈旧。

    Returns:
        {
          "signature": {(name, diagram_type), ...},
          "diagram_count": int,
          "class_diagram_count": int,
          "class_count": int,
        }
        读取失败返回 None。
    """
    try:
        from app.services.file_service import load_project
        project = load_project(project_file)
        diagrams = project.diagrams
        class_diagrams = [d for d in diagrams if d.diagram_type == "class"]
        return {
            "signature": {(d.name, d.diagram_type) for d in diagrams},
            "diagram_count": len(diagrams),
            "class_diagram_count": len(class_diagrams),
            "class_count": sum(len(d.classes) for d in class_diagrams),
        }
    except Exception:
        logger.warning("[Explore] Failed to load project file signature", exc_info=True)
        return None


def _force_rebuild_uml(project_file: str, project_id: str) -> None:
    """全量重建某项目的 design 层 KG 索引（explore 交叉校验兜底）。"""
    try:
        from knowledge_graph.builder import GraphBuilder
        from app.services.file_service import load_project
        project = load_project(project_file)
        builder = GraphBuilder(db_path=_kg_db_path())
        stats = builder.build_from_project(project, project_id)
        builder.close()
        logger.info("[Explore] Forced rebuild for '%s': %s", project_id, stats)
    except Exception:
        logger.exception("[Explore] Forced rebuild failed for '%s'", project_id)


async def _explore_uml(llm: BaseAgentsLLM, project_id: str, project_file: str, question: str) -> str:
    """uml 探索：kg_project_structure 拿完整结构 → 交叉校验 → 一次 LLM 总结。"""
    from app.agent_base.tools.my_tools.knowledge_graph_tools import KgProjectStructureTool
    struct_tool = KgProjectStructureTool(db_path=_kg_db_path(), project_file=project_file)
    try:
        raw = await struct_tool._execute({"project_id": project_id, "depth": 3})
    except Exception as e:
        return f"（UML 结构获取失败: {e}）"
    try:
        structure = json.loads(raw)
    except json.JSONDecodeError:
        return f"（UML 结构解析失败）"

    # 交叉校验：KG 结构若与文件不一致（缺图 / 类图无类），强制重建后重取。
    # 兜底 _ensure_project_indexed 的图签名比对——签名一致但内容陈旧时也能救回。
    file_sig = _project_file_signature(project_file)
    if file_sig is not None and structure.get("diagrams"):
        kg_sig = {
            (d.get("name"), d.get("diagram_type"))
            for d in structure.get("diagrams", [])
        }
        stats = structure.get("stats", {})
        stale = kg_sig != file_sig["signature"]
        if not stale and file_sig["class_count"] > 0 and stats.get("classes", 0) == 0:
            stale = True  # 文件有类图且有类，但 KG 一个类都没索引到
        if stale:
            logger.info(
                "[Explore] KG structure out of sync with file for '%s' "
                "(file_diagrams=%d, kg_diagrams=%d, file_classes=%d, kg_classes=%d), rebuilding",
                project_id, file_sig["diagram_count"], len(kg_sig),
                file_sig["class_count"], stats.get("classes", 0),
            )
            _force_rebuild_uml(project_file, project_id)
            try:
                raw = await struct_tool._execute({"project_id": project_id, "depth": 3})
                structure = json.loads(raw)
            except Exception as e:
                logger.warning("[Explore] Refetch after rebuild failed: %s", e)

    if not structure.get("diagrams"):
        return "（项目中无 UML 设计图）"

    from app.services.chat_trace import trace_span

    prompt = (
        f"根据以下项目的 UML 结构，用中文回答用户问题。\n\n"
        f"## UML 结构（完整，含所有图、类、方法、消息）\n"
        f"{json.dumps(structure, ensure_ascii=False)[:6000]}\n\n"
        f"## 用户问题\n{question}\n\n"
        f"请基于结构直接回答，输出简洁总结，覆盖: 有哪些图、每张图的组成、"
        f"关键类与方法、时序流程。控制在 1500 字符内，不要提及结构被截断。"
    )
    with trace_span("explore_project/uml_summary"):
        return await llm.ainvoke([{"role": "user", "content": prompt}])


def _list_py_files(root: str, prefix: str = "") -> list[str]:
    """列出目录下所有 .py 文件（相对路径，正斜杠）。"""
    if not root or not os.path.isdir(root):
        return []
    files = []
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            if n.endswith(".py"):
                rel = os.path.relpath(os.path.join(dirpath, n), root)
                files.append(rel.replace("\\", "/"))
    files.sort()
    return files


async def _explore_source(llm: BaseAgentsLLM, source_dir: str, question: str) -> str:
    """source 探索：列文件 → 读前几个关键文件 → LLM 总结。"""
    files = _list_py_files(source_dir)
    if not files:
        return "（项目无源代码）"

    # 读所有文件内容（源码通常不大），拼给 LLM
    chunks = []
    for rel in files:
        fpath = os.path.join(source_dir, rel)
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            chunks.append(f"### {rel}\n{content[:2000]}")
        except OSError:
            continue
    src_text = "\n\n".join(chunks)[:6000]

    from app.services.chat_trace import trace_span

    prompt = (
        f"根据以下项目的源代码文件，用中文回答用户问题。\n\n"
        f"## 源码文件\n{src_text}\n\n"
        f"## 用户问题\n{question}\n\n"
        f"请总结项目的代码结构、主要模块与职责。控制在 1500 字符内。"
    )
    with trace_span("explore_project/source_summary"):
        return await llm.ainvoke([{"role": "user", "content": prompt}])


async def _explore_test(llm: BaseAgentsLLM, test_dir: str, question: str) -> str:
    """test 探索：列测试文件 → 读内容 → LLM 总结。"""
    files = _list_py_files(test_dir)
    if not files:
        return "（项目无测试代码）"

    chunks = []
    for rel in files:
        fpath = os.path.join(test_dir, rel)
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            chunks.append(f"### {rel}\n{content[:2000]}")
        except OSError:
            continue
    test_text = "\n\n".join(chunks)[:6000]

    from app.services.chat_trace import trace_span

    prompt = (
        f"根据以下项目的测试文件，用中文回答用户问题。\n\n"
        f"## 测试文件\n{test_text}\n\n"
        f"## 用户问题\n{question}\n\n"
        f"请总结测试覆盖的范围、测试了哪些模块与功能。控制在 1500 字符内。"
    )
    with trace_span("explore_project/test_summary"):
        return await llm.ainvoke([{"role": "user", "content": prompt}])


async def _recall_inject(project_id: str, query: str, system_prompt: str) -> str:
    """检索项目记忆并注入（失败则原样返回）。"""
    if not project_id:
        return system_prompt
    try:
        from memory_system.manager import MemoryManager
        mgr = MemoryManager(db_path=_memory_db_path())
        results = await mgr.recall(project_id, query, top_k=5)
        return mgr.inject_memories(system_prompt, results)
    except Exception:
        logger.warning("[Explore] Inject memory failed (non-fatal)", exc_info=True)
        return system_prompt


async def _archive_result(project_id: str, resource_type: str, question: str, answer: str, llm: BaseAgentsLLM) -> None:
    """探索结果归档到项目记忆（异步后台）。"""
    try:
        from memory_system.manager import MemoryManager
        mgr = MemoryManager(db_path=_memory_db_path())
        async def _extract(prompt: str) -> str:
            return await llm.ainvoke([{"role": "user", "content": prompt}])
        await mgr.remember(
            project_id=project_id,
            context=f"探索{RESOURCE_LABELS.get(resource_type, resource_type)}: {question[:100]}",
            llm_call_type=f"explore_{resource_type}",
            user_input=question,
            llm_output=answer[:2000],
            extract_fn=_extract,
        )
    except Exception:
        logger.warning("[Explore] Archive memory failed (non-fatal)", exc_info=True)


async def _run_explorer(
    llm: BaseAgentsLLM,
    resource_type: str,
    project_id: str,
    project_file: str,
    source_dir: str,
    test_dir: str,
    question: str,
    mode: str = "summary",
) -> str:
    """运行单个资源探索（确定性流程，返回总结或定位信息）。"""
    # 记忆注入：给 LLM 调用提供历史上下文
    mem_sys_prompt = await _recall_inject(
        project_id, f"{resource_type} {question}",
        f"你正在探索项目的{RESOURCE_LABELS.get(resource_type, resource_type)}。",
    )

    if mode == "locate" and resource_type == "uml":
        result = await _locate_uml(llm, project_id, project_file, question)
    elif resource_type == "uml":
        result = await _explore_uml(llm, project_id, project_file, question)
    elif resource_type == "source":
        result = await _explore_source(llm, source_dir, question)
    elif resource_type == "test":
        result = await _explore_test(llm, test_dir, question)
    else:
        result = f"（未知资源类型: {resource_type}）"

    # 归档（后台）
    if project_id:
        import asyncio
        asyncio.create_task(_archive_result(project_id, resource_type, question, result, llm))
    return result


class ExploreProjectTool(AsyncTool):
    """项目探索工具 — 单一入口，探索/定位项目资源。

    主 Agent 的唯一只读入口：总结或定位项目的 uml/source/test 资源。
    避免主 Agent 直接 read_file/grep/kg_* —— 探索细节在此封装。
    """

    def __init__(
        self,
        llm: BaseAgentsLLM,
        project_file: str = "",
        source_dir: str = "",
        test_dir: str = "",
    ):
        super().__init__(
            name="explore_project",
            description=(
                "Explore or locate the project's resources in one call. This is "
                "the ONLY read tool you need for understanding the project — use "
                "it instead of reading files or querying the graph yourself. "
                "Two modes:\n"
                "- mode='summary': return a concise summary of a resource type. "
                "what='uml' (design), 'source' (code), 'test' (tests), or 'all'. "
                "question: what you want summarized.\n"
                "- mode='locate': find a specific element and return its exact "
                "location (id, line, offset) so you can act on it. "
                "what='uml', question=the element to find (e.g. 'loop fragment', "
                "a class name, a method). Use this before modifying, so you know "
                "what to target."
            ),
        )
        self.llm = llm
        self.project_file = project_file
        self.source_dir = source_dir
        self.test_dir = test_dir

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="mode",
                type="string",
                description="'summary' (default, return a summary) or 'locate' (find an element's exact location).",
                required=False,
                default="summary",
            ),
            ToolParameter(
                name="what",
                type="string",
                description="Resource: 'uml' | 'source' | 'test' | 'all'. 'all' explores whichever resources exist.",
                required=True,
            ),
            ToolParameter(
                name="question",
                type="string",
                description="For summary: what to summarize. For locate: the element to find (e.g. 'loop fragment', 'class EchoSimulator').",
                required=False,
                default="summarize the structure",
            ),
        ]

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "description": "'summary' (default, return a summary) or 'locate' (find an element's exact location).",
                        },
                        "what": {
                            "type": "string",
                            "description": "Resource: 'uml' | 'source' | 'test' | 'all'. 'all' explores whichever resources exist.",
                        },
                        "question": {
                            "type": "string",
                            "description": "For summary: what to summarize. For locate: the element to find.",
                        },
                    },
                    "required": ["what"],
                },
            },
        }

    async def _execute(self, params: dict) -> str:
        mode = str(params.get("mode", "summary")).strip().lower() or "summary"
        what = str(params.get("what", "all")).strip().lower()
        question = str(params.get("question", "summarize the structure")).strip() or "summarize the structure"

        project_id = ""
        if self.project_file and os.path.isfile(self.project_file):
            project_id = os.path.splitext(os.path.basename(self.project_file))[0]

        has_uml = bool(self.project_file and os.path.isfile(self.project_file))
        has_source = bool(self.source_dir and os.path.isdir(self.source_dir))
        has_test = bool(self.test_dir and os.path.isdir(self.test_dir))

        if what == "all":
            targets = []
            if has_uml:
                targets.append("uml")
            if has_source:
                targets.append("source")
            if has_test:
                targets.append("test")
        else:
            targets = [what] if what in RESOURCE_LABELS else []

        if not targets:
            return json.dumps({
                "error": f"No explorable resource for what='{what}'. "
                         f"Detected: uml={has_uml}, source={has_source}, test={has_test}.",
            }, ensure_ascii=False)

        import asyncio
        results = await asyncio.gather(*[
            _run_explorer(
                self.llm, rt, project_id, self.project_file,
                self.source_dir, self.test_dir, question, mode,
            )
            for rt in targets
        ])

        if mode == "locate":
            # locate 返回结构化 JSON，直接拼起来
            return "\n\n".join(results)

        sections = []
        for rt, res in zip(targets, results):
            sections.append(f"## {RESOURCE_LABELS.get(rt, rt)}\n{res}")
        return "\n\n".join(sections)


def create_explore_project_tool(
    llm: BaseAgentsLLM,
    project_file: str = "",
    source_dir: str = "",
    test_dir: str = "",
) -> Tool:
    """创建项目探索工具。"""
    return ExploreProjectTool(
        llm=llm, project_file=project_file,
        source_dir=source_dir, test_dir=test_dir,
    )
