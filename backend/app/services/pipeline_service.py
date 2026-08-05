"""Pipeline orchestration service – manages the 6-stage automation pipeline."""

from datetime import datetime
from typing import AsyncIterator
import json
import logging
import os
from app.models.uml import UmlDiagram
from app.models.pipeline import (
    PipelineState, PipelineStage, StageName, StageStatus, STAGE_LABELS,
    CodeArtifact,
)
from app.services.llm_service import chat
from app.services.code_generator import (
    generate_code, generate_tests, optimize_project,
    adapt_code_to_uml, update_tests_incremental,
    generate_integrated_code,
)
from app.core.config import get_settings
from app.core.security import safe_path, resolve_path
from app.agent_base.core.llm import BaseAgentsLLM

logger = logging.getLogger(__name__)

# ── Lazy LLM singleton for CodeValidator ────────────────────
_llm: BaseAgentsLLM | None = None


def _get_llm() -> BaseAgentsLLM:
    global _llm
    if _llm is None:
        _llm = BaseAgentsLLM.from_settings()
    return _llm


async def _run_code_validation(
    language: str,
    code_files: dict[str, str],
    task_description: str,
    max_rounds: int = 5,
    change_ratio: int = 0,
    design_constraints: dict | None = None,
    generated_dir: str = "",
    original_code: dict[str, str] | None = None,
):
    """Run code validation via CodeValidator (ReActAgent-based).

    Yields progress dicts compatible with the old ReActEngine format:
    ``{"react_steps": [...], "round": N}`` per round,
    ``{"result": {success, final_code, summary, steps, remaining_issues}}`` on completion.

    Replaces ``ReActEngine.run_code_validate_and_fix_stream()``.
    """
    from app.agent_base.tools.my_tools.code_validator import CodeValidator

    validator = CodeValidator(
        _get_llm(),
        language=language,
        max_rounds=max_rounds,
        change_ratio=change_ratio,
        design_constraints=design_constraints,
        generated_dir=generated_dir,
    )
    result = None
    async for progress in validator.validate_stream(
        code_files=code_files,
        task_description=task_description,
        original_code=original_code,
    ):
        if "result" in progress:
            result = progress["result"]
        else:
            yield progress  # intermediate progress: {react_steps, round}
    if result is None:
        raise RuntimeError("CodeValidator stream ended without result")
    yield {"result": result}


def _save_uml_review_record(diagram_name: str, action: str, comment: str = ""):
    """Append UML optimization review record to dev_review.txt."""
    settings = get_settings()
    review_file = os.path.abspath(os.path.join(settings.uml_dir, "..", "dev_review.txt"))

    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if action == "skip":
        action_label = "跳过"
    elif action == "accept":
        action_label = "接受"
    elif action == "reject":
        action_label = "拒绝"
    else:
        action_label = action

    entry = f"""============================================================
[{ts}] UML评审结果: {action_label}
优化需求: (无)
原始版本: {diagram_name}
优化版本: {diagram_name}
评审意见: {comment if comment else '(无)'}
============================================================
"""

    with open(review_file, "a", encoding="utf-8") as f:
        f.write(entry)

    logger.info(f"[Pipeline] UML review saved to dev_review.txt: {action_label}")


def _save_case_review_file(pipeline_id: str, diagram_name: str, test_cases: str):
    """Append case review summary to dev_review.txt when Stage 4 completes."""
    settings = get_settings()
    review_file = os.path.abspath(os.path.join(settings.uml_dir, "..", "dev_review.txt"))

    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    case_count = len([l for l in test_cases.split("\n") if l.strip().startswith("- [")])

    entry = f"""============================================================
[{ts}] 用例审核确认
Pipeline: {pipeline_id}
项目: {diagram_name}
用例总数: {case_count}
============================================================
"""

    with open(review_file, "a", encoding="utf-8") as f:
        f.write(entry)

    logger.info(f"[Pipeline] Case review appended to dev_review.txt: {case_count} cases")
    return review_file


def _save_pipeline_log(pipeline: PipelineState, diagram: UmlDiagram, language: str):
    """Save detailed pipeline run log as a markdown file in pipeline_log/ directory."""
    settings = get_settings()
    log_dir = os.path.abspath(os.path.join(settings.uml_dir, "..", "pipeline_log"))
    os.makedirs(log_dir, exist_ok=True)

    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c for c in diagram.name if c.isalnum() or c in "._- ").strip() or "pipeline"
    filename = f"{ts}_{safe_name}.md"
    filepath = os.path.join(log_dir, filename)

    status_icon = {"pending": "⏸", "running": "⏳", "success": "✅", "failed": "❌", "skipped": "⏭"}

    lines = [
        f"# 流水线运行报告",
        f"",
        f"| 项目 | 详情 |",
        f"|------|------|",
        f"| Pipeline ID | `{pipeline.pipeline_id}` |",
        f"| 项目名称 | {diagram.name} |",
        f"| 目标语言 | {language} |",
        f"| 运行时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |",
        f"| 总阶段数 | {len(pipeline.stages)} |",
        f"| 类数量 | {len(diagram.classes)} |",
        f"| 关系数量 | {len(diagram.relations)} |",
        f"",
        f"---",
        f"",
    ]

    # ── Class & Relation Summary ──
    if diagram.classes:
        lines.append("## UML 设计概览")
        lines.append("")
        lines.append("| 类名 | 构造型 | 属性数 | 方法数 | 备注 |")
        lines.append("|------|--------|--------|--------|------|")
        for c in diagram.classes:
            stereotype = str(c.stereotype).split(".")[-1] if hasattr(c.stereotype, 'value') else str(c.stereotype)
            lines.append(f"| {c.name} | {stereotype} | {len(c.attributes)} | {len(c.methods)} | {c.note or '-'} |")
        lines.append("")
        if diagram.relations:
            lines.append("**关系**:")
            for r in diagram.relations:
                src = next((c.name for c in diagram.classes if c.id == r.source), r.source)
                tgt = next((c.name for c in diagram.classes if c.id == r.target), r.target)
                lines.append(f"- {src} → {tgt}: `{r.type}`")
            lines.append("")

    lines.extend([
        "---",
        "",
        "## 阶段执行结果",
        "",
    ])

    # ── Stage details with rich info ──
    for i, s in enumerate(pipeline.stages, 1):
        icon = status_icon.get(s.status.value, "❓")
        lines.append(f"### Stage {i}: {s.label}")
        lines.append(f"**状态**: {icon} {s.status.value}")
        if s.logs:
            lines.append(f"**摘要**: {s.logs[:200]}")
        lines.append("")

        # Show stage-specific result data
        result = s.result or {}

        if s.name == StageName.CODE_GEN:
            files = result.get("files", [])
            react_steps = result.get("react_steps", [])
            react_summary = result.get("react_summary", "")
            react_error = result.get("react_error", "")
            lines.append(f"**生成文件**: {len(files)} 个")
            for f in files:
                lines.append(f"- `{f}`")
            if not files and s.status == StageStatus.FAILED:
                lines.append("")
                lines.append(
                    "> 💡 代码生成失败时，LLM 原始输出已保存至 `temp/pipeline_log/llm_raw_*.txt`，"
                    "请查看该文件了解模型实际返回内容。"
                )
            lines.append("")

            if react_error:
                lines.append(f"⚠️ **验证异常**: {react_error}")
                lines.append("")
            elif react_steps:
                lines.append(f"**ReAct 验证步骤**: {len(react_steps)} 步")
                if react_summary:
                    lines.append(f"**验证结果**: {react_summary}")
                lines.append("")
                lines.append("| 轮次 | 动作 | 观察结果 |")
                lines.append("|------|------|---------|")
                for step in react_steps:
                    obs = str(step.get("observation", ""))[:800]
                    action = step.get("action", "-")
                    is_final = " ✅" if step.get("is_final") else ""
                    lines.append(f"| {step.get('round', '-')} | `{action}`{is_final} | {obs} |")
                lines.append("")

        elif s.name == StageName.TEST_GEN:
            files = result.get("test_files", [])
            react_steps = result.get("react_steps", [])
            react_summary = result.get("react_summary", "")
            react_error = result.get("react_error", "")
            lines.append(f"**测试文件**: {len(files)} 个")
            for f in files:
                lines.append(f"- `{f}`")
            lines.append("")

            if react_error:
                lines.append(f"⚠️ **ReAct 校验异常**: {react_error}")
                lines.append("")
            elif react_steps:
                lines.append(f"**ReAct 测试校验步骤**: {len(react_steps)} 步")
                if react_summary:
                    lines.append(f"**校验结果**: {react_summary}")
                lines.append("")
                lines.append("| 轮次 | 动作 | 观察结果 |")
                lines.append("|------|------|---------|")
                for step in react_steps:
                    obs = str(step.get("observation", ""))[:200]
                    lines.append(f"| {step.get('round', '-')} | `{step.get('action', '-')}` | {obs} |")
                lines.append("")

        elif s.name == StageName.CODE_OPTIMIZE:
            test_results = result.get("test_results", "")
            lines.append(f"**总优化轮次**: {pipeline.optimization_round}/3")
            if test_results:
                lines.append("")
                lines.append("**测试执行结果**:")
                lines.append("```")
                lines.append(str(test_results)[:5000])
                lines.append("```")
            lines.append("")

            # Show each round's detailed results
            rounds = result.get("rounds", [])
            if rounds:
                for rd in rounds:
                    rn = rd.get("round", "?")
                    pr = rd.get("pass_rate", 0)
                    rolled_back = rd.get("rolled_back", False)
                    icon = "🎉" if pr == 100 else "⚠️" if pr >= 80 else "❌"
                    rb_tag = " **[回退]**" if rolled_back else ""
                    lines.append(f"#### Round {rn}: {icon} 通过率 {pr}%{rb_tag}")
                    lines.append(f"- 用例: {rd.get('passed', 0)} 通过 / {rd.get('failed', 0)} 失败 / {rd.get('total', 0)} 总计")

                    # Show ALL failure details
                    tr = rd.get("test_results", "")
                    if tr:
                        fail_lines = [l for l in tr.split("\n") if "-> FAIL" in l]
                        pass_lines = [l for l in tr.split("\n") if "-> PASS" in l]
                        if fail_lines:
                            lines.append("")
                            lines.append(f"**失败用例 ({len(fail_lines)} 个)**:")
                            for fl in fail_lines:
                                lines.append(f"- {fl.strip()[:150]}")
                        elif pr == 100:
                            lines.append(f"**全部通过 ({len(pass_lines)} 个用例)**")

                        # ── Per-file breakdown ──
                        file_stats = _build_per_file_stats(tr)
                        if file_stats:
                            lines.append("")
                            lines.append("**各文件通过情况**:")
                            lines.append("")
                            lines.append("| 文件 | 通过 | 失败 | 通过率 |")
                            lines.append("|------|------|------|--------|")
                            for fs in file_stats:
                                f_pr = round(fs["passed"] / fs["total"] * 100) if fs["total"] > 0 else 0
                                icon = "✅" if f_pr == 100 else "⚠️" if f_pr >= 80 else "❌"
                                lines.append(f"| {icon} {fs['name']} | {fs['passed']} | {fs['failed']} | {f_pr}% |")
                            lines.append(f"| **合计** | **{rd.get('passed', 0)}** | **{rd.get('failed', 0)}** | **{pr}%** |")

                    lines.append("")

            # Show final comparison table
            if len(rounds) > 1:
                lines.append("#### 优化效果对比")
                lines.append("")
                lines.append("| 轮次 | 通过 | 失败 | 通过率 | 备注 |")
                lines.append("|------|------|------|--------|------|")
                for rd in rounds:
                    note = "回退" if rd.get("rolled_back") else ""
                    lines.append(f"| {rd.get('round', '?')} | {rd.get('passed', 0)} | {rd.get('failed', 0)} | {rd.get('pass_rate', 0)}% | {note} |")
                lines.append("")

    # ── Code Artifacts ──
    lines.extend([
        "---",
        "",
        "## 代码产物",
        "",
    ])

    src_artifacts = [a for a in pipeline.code_artifacts if a.version == 1]
    test_artifacts = [a for a in pipeline.code_artifacts if a.version == 2]
    opt_artifacts = [a for a in pipeline.code_artifacts if a.version == 3]

    lines.append(f"### 📦 源代码 ({len(src_artifacts)} 文件)")
    for a in src_artifacts:
        lines.append(f"- `{a.filename}` ({len(a.content)} 字符, {a.content.count(chr(10))+1} 行)")
    if not src_artifacts:
        lines.append("- 无")
    lines.append("")

    lines.append(f"### 🧪 测试代码 ({len(test_artifacts)} 文件)")
    for a in test_artifacts:
        lines.append(f"- `{a.filename}` ({len(a.content)} 字符, {a.content.count(chr(10))+1} 行)")
    if not test_artifacts:
        lines.append("- 无")
    lines.append("")

    if opt_artifacts:
        lines.append(f"### 🔧 优化后代码 ({len(opt_artifacts)} 文件)")
        for a in opt_artifacts:
            lines.append(f"- `{a.filename}` ({len(a.content)} 字符)")
        lines.append("")

    # ── Final Test Pass Rate ──
    code_opt_stage = next((s for s in pipeline.stages if s.name == StageName.CODE_OPTIMIZE), None)
    if code_opt_stage and code_opt_stage.result:
        test_results_text = code_opt_stage.result.get("test_results", "")
        if test_results_text:
            # Parse pass/fail counts
            import re
            passed = len(re.findall(r'->\s*(?:✅\s*)?PASS', test_results_text, re.IGNORECASE))
            failed = len(re.findall(r'->\s*(?:❌\s*)?FAIL', test_results_text, re.IGNORECASE))
            total = passed + failed
            pass_rate = round(passed / total * 100) if total > 0 else 0

            lines.extend([
                "---",
                "",
                "## 最终测试结果",
                "",
                f"| 指标 | 数值 |",
                f"|------|------|",
                f"| 总用例数 | {total} |",
                f"| 通过 | {passed} |",
                f"| 失败 | {failed} |",
                f"| 通过率 | {pass_rate}% |",
                f"| 优化轮次 | {pipeline.optimization_round}/3 |",
                "",
            ])

            if pass_rate == 100:
                lines.append("🎉 **全部测试用例通过！**")
            elif pass_rate >= 80:
                lines.append(f"⚠️ 通过率 {pass_rate}%，仍有部分失败需人工审查。")
            else:
                lines.append(f"❌ 通过率仅 {pass_rate}%，建议重新审查代码和用例。")
            lines.append("")

    # ── Review Log ──
    if pipeline.review_log:
        lines.extend([
            "---",
            "",
            "## 评审记录",
            "",
        ])
        for entry in pipeline.review_log:
            action = "接受 ✅" if entry.get("accepted") else "拒绝 ❌"
            ts = entry.get("timestamp", "")[:19]
            lines.append(f"| {ts} | {action} | {entry.get('comment', '-')} |")

    lines.extend([
        "",
        "---",
        "",
        f"*Generated by UML Designer at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ])

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"[Pipeline] Log saved: {filepath}")
    return filepath


def _save_generated_files(
    project_name: str, language: str, src: dict, test: dict = None,
    target_src_dir: str = "", target_test_dir: str = "",
):
    """Save generated code files.

    If target_src_dir / target_test_dir are provided, save to those paths
    instead of the default generated/ directory.  User directories are NEVER
    wiped — only individual files are overwritten.
    """
    settings = get_settings()
    base = os.path.abspath(os.path.join(settings.uml_dir, "..", "..", "generated"))
    os.makedirs(base, exist_ok=True)

    result = {"src": [], "test": []}

    # Determine source directory
    if target_src_dir:
        src_dir = target_src_dir
    else:
        src_dir = os.path.join(base, "src")
    os.makedirs(src_dir, exist_ok=True)

    if src:
        for fname, content in src.items():
            fp = os.path.join(src_dir, fname)
            try:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(content)
                result["src"].append(fp.replace("\\", "/"))
                logger.info(f"[Pipeline] Saved source: {fp}")
            except Exception as e:
                logger.warning(f"[Pipeline] Failed to save {fp}: {e}")

    # Determine test directory
    is_user_dir = bool(target_test_dir)
    if target_test_dir:
        test_dir = target_test_dir
    else:
        test_dir = os.path.join(base, "test")
    os.makedirs(test_dir, exist_ok=True)

    if test:
        # Only auto-clean the internal generated/ directory, NOT user directories
        if not is_user_dir and os.path.exists(test_dir):
            import shutil
            shutil.rmtree(test_dir)
        os.makedirs(test_dir, exist_ok=True)
        for fname, content in test.items():
            fp = os.path.join(test_dir, fname)
            try:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(content)
                result["test"].append(fp.replace("\\", "/"))
                logger.info(f"[Pipeline] Saved test: {fp}")
            except Exception as e:
                logger.warning(f"[Pipeline] Failed to save {fp}: {e}")

    return result

# In-memory store (replace with DB in production)
_pipelines: dict[str, PipelineState] = {}
_stopped: set[str] = set()


async def resume_with_instructions(
    pipeline_id: str,
    diagram: UmlDiagram,
    instructions: str,
    language: str = "python",
    source_dir: str = "",
    test_dir: str = "",
    sequence_diagram: dict | None = None,
    component_diagram: dict | None = None,
    all_diagrams: list[dict] | None = None,
    max_change_ratio: int = 0,
) -> AsyncIterator[dict]:
    """Resume pipeline from Stage 1 with optimization instructions."""
    async for event in run_pipeline(
        pipeline_id, diagram, language,
        auto_confirm=False, instructions=instructions,
        source_dir=source_dir, test_dir=test_dir,
        sequence_diagram=sequence_diagram,
        component_diagram=component_diagram,
        all_diagrams=all_diagrams,
        max_change_ratio=max_change_ratio,
    ):
        yield event


def stop_pipeline(pipeline_id: str):
    """Signal a pipeline to stop and reset all RUNNING stages to FAILED."""
    _stopped.add(pipeline_id)
    pipeline = _pipelines.get(pipeline_id)
    if pipeline:
        for stage in pipeline.stages:
            if stage.status == StageStatus.RUNNING:
                stage.status = StageStatus.FAILED
                stage.logs = (stage.logs or "") + " [已停止]"


def _clear_stop_flag(pipeline_id: str):
    """Remove the stop flag for a pipeline (cleanup after stop is handled)."""
    _stopped.discard(pipeline_id)


def _is_stopped(pipeline_id: str) -> bool:
    return pipeline_id in _stopped


def create_pipeline(diagram_id: str, diagram: UmlDiagram) -> PipelineState:
    pipeline_id = f"pipe_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{diagram_id[:8]}"
    stages = [
        PipelineStage(name=StageName.UML_OPTIMIZE, label=STAGE_LABELS["uml_optimize"]),
        PipelineStage(name=StageName.DEV_CONFIRM, label=STAGE_LABELS["dev_confirm"]),
        PipelineStage(name=StageName.CODE_GEN, label=STAGE_LABELS["code_gen"]),
        PipelineStage(name=StageName.CASE_REVIEW, label=STAGE_LABELS["case_review"]),
        PipelineStage(name=StageName.TEST_GEN, label=STAGE_LABELS["test_gen"]),
        PipelineStage(name=StageName.CODE_OPTIMIZE, label=STAGE_LABELS["code_optimize"]),
    ]
    state = PipelineState(
        pipeline_id=pipeline_id,
        diagram_id=diagram_id,
        current_stage=StageName.UML_OPTIMIZE,
        stages=stages,
    )
    _pipelines[pipeline_id] = state
    return state


def get_pipeline(pipeline_id: str) -> PipelineState | None:
    return _pipelines.get(pipeline_id)


def confirm_stage(pipeline_id: str, stage: StageName, accepted: bool, comment: str = "") -> PipelineState:
    """Handle dev confirmation for a stage."""
    pipeline = _pipelines.get(pipeline_id)
    if not pipeline:
        raise ValueError(f"Pipeline not found: {pipeline_id}")

    pipeline.review_log.append({
        "stage": stage.value,
        "accepted": accepted,
        "comment": comment,
        "timestamp": datetime.now().isoformat(),
    })

    stg = next((s for s in pipeline.stages if s.name == stage), None)
    if stg:
        if accepted:
            stg.status = StageStatus.SUCCESS
        else:
            stg.status = StageStatus.FAILED
            stg.logs = f"Rejected by user: {comment}" if comment else "Rejected by user"

    return pipeline


def _ensure_constraints_coverage(diagram: UmlDiagram, constraints: dict) -> dict:
    """Safety-net: check that LLM-extracted constraints cover key structural relations.

    If the LLM missed any non-dependency relation, append it programmatically.
    Returns the (possibly augmented) constraints dict.
    """
    if not constraints:
        constraints = {}
    if not diagram or not diagram.classes:
        return constraints

    _name = {c.id: c.name for c in diagram.classes}
    type_labels = {"inheritance": "继承", "composition": "组合",
                   "aggregation": "聚合", "realization": "实现"}

    # Build set of (source_name, target_name, type_label) from UML relations
    structural: set[tuple[str, str, str]] = set()
    for rel in diagram.relations:
        if rel.type in ("dependency", "association"):
            continue  # non-structural — LLM may legitimately omit
        src = _name.get(rel.source, rel.source)
        tgt = _name.get(rel.target, rel.target)
        label = type_labels.get(rel.type, rel.type)
        structural.add((src, tgt, label))

    if not structural:
        return constraints

    # Crude check: look for all three tokens in any order in must_preserve entries
    preserved_text = " ".join(constraints.get("must_preserve", [])).lower()
    immutable = set(constraints.get("immutable_entities", []))

    added = 0
    for src, tgt, label in structural:
        if src.lower() not in preserved_text or tgt.lower() not in preserved_text:
            entry = f"{src} → {tgt} ({label}) — 不可删除或降级"
            constraints.setdefault("must_preserve", []).append(entry)
            added += 1
        if src not in immutable:
            immutable.add(src)
        if tgt not in immutable:
            immutable.add(tgt)

    if immutable:
        constraints["immutable_entities"] = sorted(immutable)

    if added:
        logger.warning(
            f"[Constraints] Safety-net added {added} missed relations: "
            f"LLM extracted {len(structural) - added}/{len(structural)}"
        )

    return constraints


async def run_pipeline(
    pipeline_id: str,
    diagram: UmlDiagram,
    language: str = "python",
    auto_confirm: bool = False,
    test_cases: list[dict] | None = None,
    instructions: str = "",
    source_dir: str = "",
    test_dir: str = "",
    sequence_diagram: dict | None = None,
    component_diagram: dict | None = None,
    all_diagrams: list[dict] | None = None,
    max_change_ratio: int = 0,
) -> AsyncIterator[dict]:
    """Run the 6-stage pipeline, yielding progress updates.

    ``all_diagrams``: full list of project diagrams for global optimization.
    ``max_change_ratio`` (0-100): when >0, Stage 3 ReAct validation will
    enforce a per-file change limit against the original code. 0 = disabled.
    """
    pipeline = _pipelines.get(pipeline_id)
    if not pipeline:
        raise ValueError(f"Pipeline not found: {pipeline_id}")

    optimized_diagram = diagram
    current_code: dict[str, str] = {}
    test_files: dict[str, str] = {}
    test_results = ""

    # --- Stage 1: UML Optimize (global: cross-validate all 3 diagram types) ---
    if _is_stopped(pipeline_id): return
    yield await _update_stage(pipeline, StageName.UML_OPTIMIZE, StageStatus.RUNNING,
                               "Requesting optimization instructions...")

    # If no instructions provided, yield a request event and wait
    if not instructions:
        yield {
            "event": "request_instructions",
            "pipeline_id": pipeline_id,
            "stage": StageName.UML_OPTIMIZE.value,
            "message": "请输入优化需求（可选留空跳过）",
        }
        return  # Wait for instructions via WebSocket, then resume

    try:
        # Global optimization: pass all existing diagrams as reference
        _diagrams_for_opt = list(all_diagrams) if all_diagrams else []
        if not _diagrams_for_opt:
            # Fallback: build list from individual diagram params
            for d in (diagram.model_dump(), sequence_diagram, component_diagram):
                if d:
                    _diagrams_for_opt.append(d)
        opt_result = await optimize_project(
            diagrams=_diagrams_for_opt if _diagrams_for_opt else None,
            instructions=instructions,
        )
        if _is_stopped(pipeline_id): return

        # Extract optimized class diagram from the diagrams array (new format)
        # Falls back to old {optimized: {class: ...}} format
        _dlist = opt_result.get("diagrams", [])
        _class_spec = next((d for d in _dlist if d.get("type") == "class"), None)
        if _class_spec and isinstance(_class_spec.get("data"), dict):
            optimized_diagram = UmlDiagram(**_class_spec["data"])
        else:
            # Old format fallback
            optimized_all = opt_result.get("optimized", {})
            optimized_class_data = optimized_all.get("class") if isinstance(optimized_all, dict) else None
            if optimized_class_data and isinstance(optimized_class_data, dict):
                optimized_diagram = UmlDiagram(**optimized_class_data)
            else:
                optimized_diagram = diagram

        pipeline.stages[0].result = opt_result
        pipeline.stages[0].logs = opt_result.get("changes_summary", "")
        # ── Extract design constraints for downstream stages ──
        _constraints = opt_result.get("design_constraints") or {}
        if _constraints:
            _constraints = _ensure_constraints_coverage(diagram, _constraints)
            pipeline.design_constraints = _constraints
            logger.info(f"[Pipeline] Design constraints: {len(_constraints.get('must_preserve', []))} preserved, "
                        f"{len(_constraints.get('immutable_entities', []))} immutable")
        yield await _update_stage(pipeline, StageName.UML_OPTIMIZE, StageStatus.SUCCESS)
    except Exception as e:
        yield await _update_stage(pipeline, StageName.UML_OPTIMIZE, StageStatus.FAILED, str(e))
        if not auto_confirm:
            return

    if _is_stopped(pipeline_id): return

    # --- Stage 2: Dev Confirm ---
    if auto_confirm:
        yield await _update_stage(pipeline, StageName.DEV_CONFIRM, StageStatus.SUCCESS)
    else:
        yield await _update_stage(pipeline, StageName.DEV_CONFIRM, StageStatus.RUNNING,
                                  "Waiting for user confirmation...")
        # In auto mode, we skip; in manual mode, pipeline pauses here
        return  # Wait for confirm_stage() call to resume

    # --- Stage 3: Code Gen ---
    # If source_dir provided and has files → adapt existing code to UML
    # Otherwise → generate from UML (existing behavior)
    if source_dir:
        existing_code = _load_files_from_directory(source_dir, language)
        if existing_code:
            yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.RUNNING,
                                       f"Adapting {len(existing_code)} existing source files to UML...")
            try:
                current_code = await adapt_code_to_uml(existing_code, optimized_diagram, language)
                if _is_stopped(pipeline_id): return
                if not current_code:
                    logger.error("[Pipeline Stage 3] adapt_code_to_uml returned empty")
                    yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.FAILED,
                                               "Code adaptation failed: LLM returned no valid code files")
                    return
                for fname, content in current_code.items():
                    pipeline.code_artifacts.append(CodeArtifact(
                        language=language, filename=fname, content=content,
                    ))
                pipeline.stages[2].result = {"files": list(current_code.keys())}
                _save_generated_files(diagram.name, language, current_code, target_src_dir=source_dir)
                pipeline.stages[2].logs = f"Adapted {len(existing_code)} existing files → {len(current_code)} files from: {source_dir}"
                logger.info("[Pipeline Stage 3] Code adaptation done, proceeding to validation")
            except Exception as e:
                yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.FAILED, str(e))
                return

    if not current_code:
        # No existing code → generate from UML (with sequence diagram if available)
        use_integrated = sequence_diagram and sequence_diagram.get("lifelines")
        yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.RUNNING,
                                   "综合生成（类图+时序图）" if use_integrated else "生成代码...")
        try:
            if use_integrated:
                current_code = await generate_integrated_code(
                    optimized_diagram.model_dump(), sequence_diagram, language)
            else:
                current_code = await generate_code(optimized_diagram, language)
                pipeline.stages[2].result["prompt_type"] = "generate_code (class only)"
            if _is_stopped(pipeline_id): return
            if not current_code:
                logger.error("[Pipeline Stage 3] generate_code returned empty — LLM JSON parse likely failed")
                yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.FAILED,
                                           "Code generation failed: LLM returned no valid code files")
                return
            for fname, content in current_code.items():
                pipeline.code_artifacts.append(CodeArtifact(
                    language=language, filename=fname, content=content,
                ))
            pipeline.stages[2].result = {"files": list(current_code.keys())}
            pipeline.stages[2].logs = "Generated: generated/src/"
            logger.info("[Pipeline Stage 3] Code generation done")

        except Exception as e:
            yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.FAILED, str(e))
            return

    # ── Write generated code to disk BEFORE ReAct validation ──
    # This lets the LLM inspect and validate files directly with run_bash
    # instead of relying on in-memory code_files parameters.
    if current_code:
        _save_generated_files(diagram.name, language, current_code, target_src_dir=source_dir)
        logger.info(f"[Pipeline Stage 3] Saved {len(current_code)} files to disk")

    # ═══════════════════════════════════════════════════════════════
    # Stage 3b: ReAct Code Validation Gate
    # ═══════════════════════════════════════════════════════════════
    if current_code:
        yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.RUNNING,
                                   "ReAct validating generated code...")
        try:
            _ratio = max_change_ratio if source_dir else 0
            _gen_dir = source_dir or os.path.abspath(
                os.path.join(get_settings().uml_dir, "..", "..", "generated", "src"))
            react_result = None
            async for progress in _run_code_validation(
                language=language,
                code_files=current_code,
                task_description="Validate generated code — fix syntax, import, and runtime errors",
                max_rounds=5,
                change_ratio=_ratio,
                design_constraints=pipeline.design_constraints,
                generated_dir=_gen_dir,
            ):
                if "result" in progress:
                    react_result = progress["result"]
                else:
                    pipeline.stages[2].result = {
                        **(pipeline.stages[2].result or {}),
                        "files": list(current_code.keys()),
                        "react_steps": progress["react_steps"],
                    }
                    pipeline.stages[2].logs = f"ReAct round {progress['round']}/{5}..."
                    yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.RUNNING)

            if react_result is None:
                raise RuntimeError("ReAct stream ended without result")

            if react_result["success"]:
                final_code = react_result.get("final_code", {})
                if final_code and final_code != current_code:
                    logger.info("[Stage 3] ReAct fixed code, updating current_code")
                    current_code = final_code
                    for fname, content in current_code.items():
                        for a in pipeline.code_artifacts:
                            if a.filename == fname and a.version == 1:
                                a.content = content
                    _save_generated_files(diagram.name, language, current_code, target_src_dir=source_dir)

                pipeline.stages[2].result = {
                    **(pipeline.stages[2].result or {}),
                    "files": list(current_code.keys()),
                    "react_steps": react_result.get("steps", []),
                    "react_summary": react_result.get("summary", ""),
                }
                pipeline.stages[2].logs = f"Validated: {len(current_code)} files OK"
                yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.SUCCESS)
            else:
                pipeline.stages[2].result = {
                    **(pipeline.stages[2].result or {}),
                    "files": list(current_code.keys()),
                    "react_steps": react_result.get("steps", []),
                    "react_summary": react_result.get("summary", ""),
                    "remaining_issues": react_result.get("remaining_issues", ""),
                }
                _rounds = len(react_result.get("steps", []))
                pipeline.stages[2].logs = f"Validation incomplete after {_rounds} rounds"
                yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.FAILED,
                                           react_result.get("summary", ""))
                return
        except Exception as e:
            logger.exception(f"[Stage 3] ReAct validation crashed: {e}")
            pipeline.stages[2].result = {
                **(pipeline.stages[2].result or {}),
                "react_error": str(e),
            }
            pipeline.stages[2].logs = f"Generated (validation skipped: {e})"
            yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.SUCCESS)

    # --- Stage 4: Case Review ---
    yield await _update_stage(pipeline, StageName.CASE_REVIEW, StageStatus.RUNNING,
                               "请在主画布检视用例，确认后继续")
    # Pause for user to review test cases
    yield {
        "event": "request_case_review",
        "pipeline_id": pipeline_id,
        "stage": StageName.CASE_REVIEW.value,
        "message": "请检视并修改用例，完成后点击确认继续",
    }
    return  # Wait for confirm via WebSocket, then resume


async def _run_test_and_optimize(
    pipeline: PipelineState,
    diagram: UmlDiagram,
    language: str,
    current_code: dict,
    test_files: dict,
    source_dir: str = "",
    test_dir: str = "",
) -> AsyncIterator[dict]:
    """Stage 6: run real pytest and optimize source code using test results.

    Test execution is folded into the code optimisation stage — no separate
    compile-check stage.  ReAct test validation already happened in Stage 5b.
    """

    if _is_stopped(pipeline.pipeline_id): return

    # ═══════════════════════════════════════════════════════════════
    # Stage 6: Run tests → optimize source code using real failures
    # ═══════════════════════════════════════════════════════════════

    # Step 1: Initial test execution
    yield await _update_stage(pipeline, StageName.CODE_OPTIMIZE, StageStatus.RUNNING,
                               "Running pytest to establish baseline...")

    test_results_text = await _execute_tests(test_files, language, diagram.name,
                                              source_dir=source_dir, test_dir=test_dir)
    total_tests = _count_tests(test_results_text)
    fatal_errors = _extract_fatal_errors(test_results_text)

    if fatal_errors:
        logger.warning(f"[Stage 6] {len(fatal_errors)} fatal errors remain after ReAct validation")
        import re as _re2
        passed = len(_re2.findall(r'->\s*PASS', test_results_text, _re2.IGNORECASE))
        failed = len(_re2.findall(r'->\s*FAIL', test_results_text, _re2.IGNORECASE))
        if passed == 0 and failed == 0:
            # No tests collected at all — compilation is fundamentally broken
            logger.error(f"[Stage 6] No tests collected — fatal compilation errors block execution")
            pipeline.stages[5].result = {
                "test_results": test_results_text,
                "unfixed_fatal_errors": fatal_errors[:10],
                "test_files": list(test_files.keys()),
            }
            yield await _update_stage(pipeline, StageName.CODE_OPTIMIZE, StageStatus.FAILED,
                                       f"Compilation still broken after validation: {len(fatal_errors)} fatal errors. "
                                       f"Check test code manually.")
            return

    pipeline.stages[5].result = {
        "test_results": test_results_text,
        "test_files": list(test_files.keys()),
    }
    yield await _update_stage(pipeline, StageName.CODE_OPTIMIZE, StageStatus.RUNNING,
                               f"Baseline: {total_tests} tests ready, starting code optimization")

    # ═══════════════════════════════════════════════════════════════
    # Stage 6 (cont.): Code Optimize — use real test results to fix source
    # ═══════════════════════════════════════════════════════════════
    rounds_history = []
    round_context = ""
    prev_failed_names: set[str] = set()
    stale_count = 0
    prev_pass_rate = 0

    for round_num in range(1, 4):
        if _is_stopped(pipeline.pipeline_id): return
        pipeline.optimization_round = round_num
        yield await _update_stage(pipeline, StageName.CODE_OPTIMIZE, StageStatus.RUNNING,
                                   f"Code optimization round {round_num}/3")

        # Save snapshot before optimization
        prev_code = {**current_code}

        # Ask LLM to fix source code based on real test failures
        optimized_code = await _optimize_source_from_tests(
            current_code, test_files, test_results_text, language, round_num,
            round_context=round_context,
        )
        if _is_stopped(pipeline.pipeline_id): return
        if optimized_code and _validate_files_match(optimized_code, current_code, "Stage6 optimize"):
            current_code = optimized_code

        # Save optimized source code
        _save_generated_files(diagram.name, language, current_code, target_src_dir=source_dir)

        # Re-run real pytest to verify
        new_test_results = await _execute_tests(test_files, language, diagram.name,
                                                 source_dir=source_dir, test_dir=test_dir)

        # Parse results
        import re as _re
        passed = len(_re.findall(r'->\s*PASS', new_test_results, _re.IGNORECASE))
        failed = len(_re.findall(r'->\s*FAIL', new_test_results, _re.IGNORECASE))
        total = passed + failed
        pass_rate = round(passed / total * 100) if total > 0 else 0

        # Extract failing test names for staleness tracking
        new_failed_names = set(_re.findall(
            r'Test:\s*\S+::(\S+)\s*->\s*FAIL', new_test_results
        ))

        # Rollback on regression
        rolled_back = False
        if round_num > 1 and pass_rate < prev_pass_rate:
            logger.warning(f"[Stage 6] Round {round_num}: REGRESSION {prev_pass_rate}% → {pass_rate}%, rolling back")
            current_code = prev_code
            _save_generated_files(diagram.name, language, current_code, target_src_dir=source_dir)
            rolled_back = True
            new_test_results = await _execute_tests(test_files, language, diagram.name,
                                                     source_dir=source_dir, test_dir=test_dir)
            passed = len(_re.findall(r'->\s*PASS', new_test_results, _re.IGNORECASE))
            failed = len(_re.findall(r'->\s*FAIL', new_test_results, _re.IGNORECASE))
            total = passed + failed
            pass_rate = round(passed / total * 100) if total > 0 else 0
            new_failed_names = set(_re.findall(r'Test:\s*\S+::(\S+)\s*->\s*FAIL', new_test_results))

        # Build round context for next round (Direction A: inter-round memory)
        prev_stats = f"Round {round_num}: {passed}P/{failed}F/{total}T = {pass_rate}%"
        change_note = ""
        if prev_failed_names:
            fixed_this_round = prev_failed_names - new_failed_names
            still_failing = prev_failed_names & new_failed_names
            new_failures = new_failed_names - prev_failed_names
            parts = []
            if fixed_this_round:
                parts.append(f"Fixed: {', '.join(sorted(fixed_this_round))}")
            if still_failing:
                parts.append(f"Still failing: {', '.join(sorted(still_failing))}")
            if new_failures:
                parts.append(f"New failures: {', '.join(sorted(new_failures))}")
            change_note = "; ".join(parts)
        round_context = f"{prev_stats}\n{change_note}\n"

        # Detect stale failures (Direction D: early exit)
        stale_now = prev_failed_names & new_failed_names if round_num > 1 else set()
        if stale_now == new_failed_names and round_num > 1:
            stale_count += 1
        else:
            stale_count = 0

        logger.info(f"[Stage 6] Round {round_num}: {passed}P/{failed}F/{total}T = {pass_rate}%"
                    f"{' (rolled back)' if rolled_back else ''} | Stale: {stale_count}")
        logger.info(f"[Stage 6] Context: {round_context.strip()}")

        round_record = {
            "round": round_num,
            "test_results": new_test_results,
            "passed": passed,
            "failed": failed,
            "total": total,
            "pass_rate": pass_rate,
            "rolled_back": rolled_back,
            "round_context": round_context.strip(),
        }
        rounds_history.append(round_record)

        pipeline.stages[5].result = {**(pipeline.stages[5].result or {}), "test_results": new_test_results, "rounds": rounds_history}

        # Early exit: all tests pass
        if failed == 0:
            yield await _update_stage(pipeline, StageName.CODE_OPTIMIZE, StageStatus.SUCCESS,
                                       f"All {total} tests pass after round {round_num}")
            break

        # Early exit: same failures persist across rounds — likely not a source code issue
        if stale_count >= 2:
            stale_list = ", ".join(sorted(new_failed_names))
            msg = f"以下 {len(new_failed_names)} 个用例在 {round_num} 轮优化后无改善，可能不是源码问题，请人工审查：{stale_list}"
            logger.warning(f"[Stage 6] Early exit: {msg}")
            yield await _update_stage(pipeline, StageName.CODE_OPTIMIZE, StageStatus.SUCCESS, msg)
            break

        prev_pass_rate = pass_rate
        prev_failed_names = new_failed_names
        test_results_text = new_test_results

    else:
        yield await _update_stage(pipeline, StageName.CODE_OPTIMIZE, StageStatus.SUCCESS,
                                   "Max optimization rounds reached")

    # Update pipeline artifacts with final code
    for fname, content in current_code.items():
        found = False
        for a in pipeline.code_artifacts:
            if a.filename == fname and a.version == 1:
                a.content = content
                found = True
                break
        if not found:
            pipeline.code_artifacts.append(CodeArtifact(
                language=language, filename=fname, content=content, version=3,
            ))


def _files_equal(a: dict[str, str], b: dict[str, str]) -> bool:
    """Check if two file dicts have identical content."""
    if set(a.keys()) != set(b.keys()):
        return False
    return all(a[k] == b[k] for k in a)


def _validate_files_match(new_files: dict, original: dict, tag: str) -> bool:
    """P1-5: Validate that LLM-returned files match original filenames exactly.
    Warns and returns False if files don't match."""
    if not isinstance(new_files, dict):
        logger.warning(f"[{tag}] LLM returned non-dict: {type(new_files)}")
        return False
    orig_set = set(original.keys())
    new_set = set(new_files.keys())
    missing = orig_set - new_set
    extra = new_set - orig_set
    if missing:
        logger.warning(f"[{tag}] LLM output MISSING files: {missing}")
    if extra:
        logger.warning(f"[{tag}] LLM output has EXTRA files: {extra}")
    if missing or extra:
        return False
    if len(new_files) != len(original):
        logger.warning(f"[{tag}] File count mismatch: expected {len(original)}, got {len(new_files)}")
        return False
    return True


def _extract_fatal_errors(test_results_text: str) -> list[str]:
    """Extract only compilation-level errors from test results.

    Fatal = code cannot even run (bad imports, syntax, undefined names).
    NOT fatal = AssertionError (test logic), object-level AttributeError
    (missing method — that's Stage 6's job to add to source code).
    """
    import re
    fatal_keywords = [
        "ImportError", "ModuleNotFoundError", "SyntaxError", "NameError",
        "cannot import", "No module named", "Can't instantiate",
    ]
    errors = []
    for line in test_results_text.split("\n"):
        if "-> FAIL" not in line:
            continue
        for kw in fatal_keywords:
            if kw in line:
                errors.append(line.strip())
                break
        else:
            # Module-level AttributeError (e.g. "module 'X' has no attribute 'Y'")
            # → fatal because the import path is wrong.
            # Object-level AttributeError (e.g. "'Foo' object has no attribute 'bar'")
            # → NOT fatal, let Stage 6 add the missing method to source code.
            if "has no attribute" in line and "' object has no attribute" not in line:
                errors.append(line.strip())
            elif "does not have the attribute" in line:
                errors.append(line.strip())
    return errors


def _count_tests(test_results_text: str) -> int:
    """Count total tests from formatted output."""
    import re
    passed = len(re.findall(r'->\s*PASS', test_results_text))
    failed = len(re.findall(r'->\s*FAIL', test_results_text))
    return passed + failed


async def _optimize_source_from_tests(
    source_code: dict[str, str],
    test_files: dict[str, str],
    test_results: str,
    language: str,
    round_num: int,
    round_context: str = "",
) -> dict[str, str]:
    """Ask LLM to optimize source code based on real pytest failures.

    Returns updated source code only (test code is never modified by Stage 6).
    The LLM receives the test code for context and previous round history so it
    can build on prior fixes rather than starting from scratch each round.
    """
    src_text = "\n\n".join(
        f"### {fname}\n```{language}\n{content}\n```"
        for fname, content in source_code.items()
    )
    test_text = "\n\n".join(
        f"### {fname}\n```{language}\n{content}\n```"
        for fname, content in test_files.items()
    )
    failures = [l for l in test_results.split("\n") if "-> FAIL" in l]

    context_block = ""
    if round_context:
        context_block = f"""## Previous Round Results:
{round_context}

"""

    prompt = f"""Fix the source code to make the failing tests pass.  You may ONLY modify source files.

{context_block}## Source Code (modify this):
{src_text[:6000]}

## Test Code (for reference — understand what API the tests expect):
{test_text[:4000]}

## Test Failures ({len(failures)} failing):
{chr(10).join(failures[:30])}

## Rules:
- If previous round tried to fix something and it still fails → try a DIFFERENT approach this round
- If a test tries to access a function/attribute that does not exist → add it to the source
- If a test gets an AssertionError → analyze the actual vs expected values and fix the implementation logic
- Keep the public API compatible with ALL tests (passing AND failing)
- Return the COMPLETE corrected source files as a JSON object

```json
{{"file1.py": "full corrected content...", "file2.py": "full corrected content..."}}
```
Only output the JSON object, no other text.
"""
    logger.info(f"[Stage6] Round {round_num}: asking LLM to fix {len(failures)} test failures (source only)")

    response = await chat(
        prompt=prompt,
        system_prompt=f"You are an expert {language} developer. Fix source code to make tests pass. Output only valid JSON.",
        temperature=0.3,
        max_tokens=8192,
        json_mode=True,
    )
    try:
        from app.services.tools import clean_llm_json_response
        cleaned = clean_llm_json_response(response)
        fixed = json.loads(cleaned)
        if _validate_files_match(fixed, source_code, "Stage6 optimize"):
            logger.info(f"[Stage6] LLM returned optimized source files: {list(fixed.keys())}")
            result = {**source_code}
            for k in source_code:
                if k in fixed:
                    result[k] = fixed[k]
            return result
        else:
            logger.warning(f"[Stage6] LLM returned invalid files, keeping originals")
            return source_code
    except Exception as e:
        logger.warning(f"[Stage6] Failed to parse LLM optimization: {e}")
        return source_code


def _load_files_from_directory(dir_path: str, language: str) -> dict[str, str]:
    """Load code files from an arbitrary directory. Returns {filename: content}.

    Returns {} if the directory is empty, doesn't exist, or path is invalid.
    """
    if not dir_path:
        return {}

    from pathlib import Path

    # Normalise the path (no project-boundary restriction for pipeline use)
    try:
        resolved = resolve_path(dir_path)
    except Exception as e:
        logger.warning(f"[Loader] Path rejected: {dir_path} — {e}")
        return {}

    target = Path(resolved)
    if not target.exists() or not target.is_dir():
        logger.warning(f"[Loader] Directory not found or not a directory: {resolved}")
        return {}

    VALID_EXTENSIONS = {
        ".py", ".ts", ".js", ".java", ".go", ".rs", ".rb", ".swift", ".kt",
        ".php", ".cs", ".cpp", ".h", ".hpp",
    }

    result = {}
    for fp in target.iterdir():
        if fp.is_file() and fp.suffix in VALID_EXTENSIONS:
            try:
                result[fp.name] = fp.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"[Loader] Failed to read {fp}: {e}")

    logger.info(f"[Loader] Loaded {len(result)} files from: {resolved}")
    return result


def _load_source_from_disk(project_name: str, language: str) -> dict[str, str]:
    """Load source files from generated/src/ directory."""
    from pathlib import Path
    settings = get_settings()
    src_dir = Path(settings.uml_dir).resolve().parent.parent / "generated" / "src"
    return _load_files_from_directory(str(src_dir), language)


def _build_per_file_stats(test_results: str) -> list[dict]:
    """Parse test results to build per-file (per-module) pass/fail stats.
    Maps test case ID prefixes (TC_BASE, TC_OTA, etc.) to module names."""
    import re

    # Maps test case ID prefix → display name (ordered by typical UML modules)
    PREFIX_MAP: dict[str, str] = {}
    file_order = []

    stats: dict[str, dict] = {}
    for line in test_results.split("\n"):
        # Match "Test: test_TC_XXXX_NNN_..." lines
        m = re.match(r'Test:\s*test_(\w+?)_\d+', line)
        if not m:
            # Also try "Test: TestClass::test_TC_XXXX_NNN_..." format (with ::)
            m = re.match(r'Test:\s*\w+::test_(\w+?)_\d+', line)
        if not m:
            continue
        prefix = m.group(1)  # e.g., "TC_BASE", "TC_OTA"

        # Derive module name from prefix: TC_BASE → BaseTask, etc.
        if prefix not in PREFIX_MAP:
            KNOWN = {
                "TC_BASE": "BaseTask",
                "TC_OTA": "OtaTask",
                "TC_CROW": "CrowTask",
                "TC_SEN": "SentinelTask",
                "TC_SCH": "TaskScheduler",
                "TC_MM": "MMApp",
            }
            if prefix in KNOWN:
                PREFIX_MAP[prefix] = KNOWN[prefix]
            else:
                # Fallback: auto-derive, capitalize each part after TC_
                parts = prefix.split("_")
                raw = "".join(p.capitalize() for p in parts[1:]) if parts[0] == "TC" else prefix
                PREFIX_MAP[prefix] = raw
            file_order.append(prefix)

        name = PREFIX_MAP[prefix]
        if name not in stats:
            stats[name] = {"name": name, "passed": 0, "failed": 0, "total": 0}
        if "-> PASS" in line:
            stats[name]["passed"] += 1
        elif "-> FAIL" in line:
            stats[name]["failed"] += 1
        stats[name]["total"] += 1

    # Return in file_order to maintain consistent display
    result = []
    for prefix in file_order:
        name = PREFIX_MAP.get(prefix)
        if name and name in stats:
            result.append(stats[name])
    # Add any unmapped
    for name, s in stats.items():
        if s not in result:
            result.append(s)
    return result


async def _update_stage(
    pipeline: PipelineState,
    stage_name: StageName,
    status: StageStatus,
    logs: str = "",
) -> dict:
    """Update a pipeline stage and return a progress event."""
    pipeline.current_stage = stage_name
    for stage in pipeline.stages:
        if stage.name == stage_name:
            stage.status = status
            if logs:
                stage.logs = logs
    return {
        "event": "stage_update",
        "pipeline_id": pipeline.pipeline_id,
        "stage": stage_name.value,
        "status": status.value,
        "logs": logs,
        "data": pipeline.model_dump(),
    }


async def _execute_tests(
    test_files: dict[str, str],
    language: str,
    project_name: str = "Untitled",
    source_dir: str = "",
    test_dir: str = "",
) -> str:
    """Execute tests using real pytest subprocess. Falls back to LLM simulation if pytest unavailable.

    If source_dir / test_dir are provided, use them instead of the auto-generated paths.
    """
    import sys
    import asyncio as _asyncio
    from pathlib import Path

    settings = get_settings()
    base_dir = Path(settings.uml_dir).resolve().parent.parent
    src_dir = Path(source_dir) if source_dir else base_dir / "generated" / "src"
    test_dir_path = Path(test_dir) if test_dir else base_dir / "generated" / "test"

    logger.info(f"[TestExec] ========== Running real pytest ==========")
    logger.info(f"[TestExec] Project: {project_name} | Language: {language}")
    logger.info(f"[TestExec] Source dir: {src_dir}")
    logger.info(f"[TestExec] Test dir:   {test_dir_path}")
    logger.info(f"[TestExec] Files on disk: test_dir exists={test_dir_path.exists()}, src_dir exists={src_dir.exists()}")

    # List actual files on disk for debugging
    if test_dir_path.exists():
        disk_files = list(test_dir_path.iterdir())
        logger.info(f"[TestExec] Test files on disk: {[f.name for f in disk_files if f.is_file()]}")
    if src_dir.exists():
        disk_src = list(src_dir.iterdir())
        logger.info(f"[TestExec] Source files on disk: {[f.name for f in disk_src if f.is_file()]}")

    if not test_dir_path.exists() or not any(test_dir_path.iterdir()):
        logger.error(f"[TestExec] Test directory empty or missing: {test_dir_path}")
        return "Error: No test files found on disk. Test generation may have failed."

    # Clear __pycache__ to avoid stale import-file-mismatch errors
    # (happens when test files move between directories, e.g. generated/test/Untitled/python/ → generated/test/)
    import shutil as _shutil
    for _root, _dirs, _files in os.walk(str(test_dir_path)):
        for _d in _dirs:
            if _d == "__pycache__":
                _cache = os.path.join(_root, _d)
                try:
                    _shutil.rmtree(_cache)
                    logger.info(f"[TestExec] Cleared pycache: {_cache}")
                except Exception:
                    pass
    # Also clear pycache from source directory
    if src_dir.exists():
        for _root, _dirs, _files in os.walk(str(src_dir)):
            for _d in _dirs:
                if _d == "__pycache__":
                    _cache = os.path.join(_root, _d)
                    try:
                        _shutil.rmtree(_cache)
                    except Exception:
                        pass

    # Build environment with PYTHONPATH including both src and test dirs
    env = os.environ.copy()
    pythonpath_parts = [str(src_dir), str(test_dir_path)]
    if "PYTHONPATH" in env:
        pythonpath_parts.insert(0, env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    # Build pytest command: verbose, short traceback, timeout per test
    cmd = [
        sys.executable, "-m", "pytest",
        str(test_dir_path),
        "-v",                    # verbose: shows each test name
        "--tb=short",            # short traceback for failures
        "--timeout=30",          # 30s timeout per test
        "--color=no",            # no ANSI colors
    ]

    logger.info(f"[TestExec] Command: {' '.join(cmd)}")
    logger.info(f"[TestExec] PYTHONPATH: {env['PYTHONPATH']}")

    try:
        # Use asyncio.to_thread + subprocess.run to avoid Windows
        # asyncio.create_subprocess_exec NotImplementedError.
        import subprocess as _sp
        result = await _asyncio.to_thread(
            _sp.run,
            cmd,
            stdout=_sp.PIPE,
            stderr=_sp.PIPE,
            env=env,
            cwd=str(base_dir),
            timeout=120,
        )

        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")

        logger.info(f"[TestExec] pytest exit code: {result.returncode}")
        logger.info(f"[TestExec] pytest stdout ({len(stdout)} chars)")

        # Debug: show first 3 test lines to verify parse format
        test_lines_raw = [l.strip() for l in stdout.split("\n") if "::" in l and ("PASSED" in l or "FAILED" in l)]
        if test_lines_raw:
            logger.info(f"[TestExec] Raw test lines ({len(test_lines_raw)} total), first 2: {test_lines_raw[:2]}")

        if stderr.strip():
            logger.info(f"[TestExec] pytest stderr ({len(stderr)} chars):\n{stderr[:800]}")

        # Parse pytest verbose output into the expected format
        formatted = _parse_pytest_output(stdout, stderr)
        # Debug: show first 2 formatted lines
        formatted_lines = [l for l in formatted.split("\n") if "->" in l]
        if formatted_lines:
            logger.info(f"[TestExec] Formatted ({len(formatted_lines)} tests), first 2: {formatted_lines[:2]}")
            logger.info(f"[TestExec] Parsed summary: {formatted.split(chr(10))[-1]}")
        logger.info(f"[TestExec] ========== pytest done ==========")

        return formatted

    except _asyncio.TimeoutError:
        logger.error("[TestExec] pytest timed out after 120s, falling back to LLM simulation")
    except FileNotFoundError:
        logger.error("[TestExec] pytest not found, falling back to LLM simulation")
    except NotImplementedError:
        logger.error("[TestExec] asyncio subprocess not supported on this platform, falling back to LLM simulation")
    except Exception as e:
        logger.exception(f"[TestExec] pytest execution failed: {e}, falling back to LLM simulation")

    # ── LLM simulation fallback ──
    logger.warning("[TestExec] ========== LLM SIMULATION MODE (not real pytest!) ==========")
    logger.warning("[TestExec] Results below are LLM PREDICTIONS, not actual code execution")

    test_code = "\n\n".join(
        f"### {fname}\n```\n{content}\n```"
        for fname, content in test_files.items()
    )
    prompt = f"""Analyze the following {language} test code and predict the test execution results.
For each test, indicate PASS or FAIL with a brief reason.
Be realistic – identify likely failures, edge cases, missing imports, etc.

{test_code}

Output format:
```
Test: test_name_1 -> PASS
Test: test_name_2 -> FAIL (reason)
...
Summary: X passed, Y failed
```
"""
    result = await chat(prompt, system_prompt="You are a test execution simulator.", temperature=0.3)
    logger.warning(f"[TestExec] LLM simulation result: {result[:300]}")
    logger.warning("[TestExec] ========== LLM simulation done ==========")
    return result


def _parse_pytest_output(stdout: str, stderr: str) -> str:
    """Parse pytest -v output into the expected format:
    Test: <name> -> PASS
    Test: <name> -> FAIL (<reason>)
    ...
    Summary: X passed, Y failed
    """
    import re
    lines_out: list[str] = []
    passed = 0
    failed = 0

    # Parse per-test lines: "test_file.py::test_name PASSED [%]" or "FAILED [%]"
    test_pattern = re.compile(r'^(.+?)::(.+?)\s+(PASSED|FAILED|ERROR|SKIPPED)')
    match_count = 0
    for line in stdout.split("\n"):
        stripped = line.strip()
        m = test_pattern.match(stripped)
        if m:
            match_count += 1
            test_name = m.group(2)
            status = m.group(3)
            if status == "PASSED":
                lines_out.append(f"Test: {test_name} -> PASS")
                passed += 1
            elif status == "FAILED":
                # Try to find the error reason from stderr or subsequent lines
                reason = _extract_failure_reason(test_name, stdout, stderr)
                lines_out.append(f"Test: {test_name} -> FAIL ({reason})")
                failed += 1
            else:
                lines_out.append(f"Test: {test_name} -> {status}")

    if not lines_out:
        # Fallback: try to find any test results in the output
        passed_match = re.search(r'(\d+)\s+passed', stdout)
        failed_match = re.search(r'(\d+)\s+failed', stdout)
        if passed_match:
            passed = int(passed_match.group(1))
        if failed_match:
            failed = int(failed_match.group(1))
        if passed or failed:
            # Build synthetic lines from summary only
            for _ in range(passed):
                lines_out.append("Test: unknown -> PASS")
            for _ in range(failed):
                lines_out.append("Test: unknown -> FAIL (see pytest output for details)")
        else:
            lines_out.append(stdout[:2000])
            lines_out.append(stderr[:1000])

    lines_out.append(f"Summary: {passed} passed, {failed} failed")
    return "\n".join(lines_out)


def _extract_failure_reason(test_name: str, stdout: str, stderr: str) -> str:
    """Extract a brief failure reason from pytest --tb=short output."""
    import re
    combined = stdout + "\n" + stderr

    # Normalize test_name to match pytest's format (replace :: with .)
    pytest_test_name = test_name.replace("::", ".")

    # Strategy 1: Find the FAILURES section block for this test
    # Format: "________ TestClass.test_name ________"
    # The block content until the next "________" or "========" separator
    escaped = re.escape(pytest_test_name)
    fail_header = re.search(
        r'_{5,}\s*' + escaped + r'\s*_{5,}\s*\r?\n(.*?)(?:\r?\n\s*_{5,}|\r?\n\s*={5,}|\Z)',
        combined, re.DOTALL
    )
    if fail_header:
        block = fail_header.group(1)
        # Extract "E   ErrorType: message" line
        err_match = re.search(r'\nE\s+(\w+(?:Error|Exception|Warning|AssertionError)):?\s*(.+?)(?:\r?\n|$)', block)
        if err_match:
            err_type = err_match.group(1)
            err_msg = err_match.group(2).strip()[:100]
            return f"{err_type}: {err_msg}"

        # Fallback: find any assertion or error line
        for pattern in [r'(AssertionError:?\s*.+)', r'(assert\s+.+)']:
            m = re.search(pattern, block)
            if m:
                return m.group(1).strip()[:100]

    # Strategy 2: Search combined output for error line near test name
    escaped_short = re.escape(pytest_test_name.split(".")[-1])  # Just the function name
    near_test = re.search(
        escaped_short + r'.*?\n(E\s+.+?)(?:\r?\n|$)',
        combined, re.MULTILINE
    )
    if near_test:
        return near_test.group(1).strip()[:100]

    return "Test assertion failed"


async def resume_pipeline(
    pipeline_id: str,
    diagram: UmlDiagram,
    language: str = "python",
    auto_confirm: bool = True,
    skip_case_review: bool = False,
    skip_code_gen: bool = False,
    source_dir: str = "",
    test_dir: str = "",
    sequence_diagram: dict | None = None,
    component_diagram: dict | None = None,
    max_change_ratio: int = 0,
) -> AsyncIterator[dict]:
    """Resume pipeline from the dev_confirm stage or after case review."""
    pipeline = _pipelines.get(pipeline_id)
    if not pipeline:
        raise ValueError(f"Pipeline not found: {pipeline_id}")

    if _is_stopped(pipeline_id):
        return

    current_code: dict[str, str] = {}
    test_files: dict[str, str] = {}

    need_code_gen = not skip_code_gen
    if skip_code_gen:
        # Reuse existing source code: user dir → artifacts → generated/ disk
        if source_dir:
            current_code = _load_files_from_directory(source_dir, language)
        if not current_code:
            current_code = {a.filename: a.content for a in pipeline.code_artifacts if a.version == 1}
        if not current_code:
            current_code = _load_source_from_disk(diagram.name, language)
        if current_code:
            logger.info(f"[Pipeline] Loaded {len(current_code)} cached source files from: {source_dir or 'artifacts/disk'}: {list(current_code.keys())}")
            # Don't re-update Stage 3 — it was already SUCCESS from the first run
        else:
            logger.warning("[Pipeline] No cached source code found, regenerating...")
            need_code_gen = True

    if need_code_gen:
        # Use optimized diagram if available from stage 1
        # New format: {diagrams: [{type, name, data}, ...]}
        # Old format: {optimized: {class: ..., sequence: ..., component: ...}}
        opt_result = pipeline.stages[0].result or {}
        _dlist = opt_result.get("diagrams", [])
        _class_spec = next((d for d in _dlist if d.get("type") == "class"), None)
        if _class_spec and isinstance(_class_spec.get("data"), dict):
            optimized_diagram = UmlDiagram(**_class_spec["data"])
        else:
            optimized_all = opt_result.get("optimized", {})
            optimized_data = optimized_all.get("class") if isinstance(optimized_all, dict) else None
            optimized_diagram = UmlDiagram(**optimized_data) if optimized_data else diagram

        # --- Stage 3: Code Gen ---
        if source_dir:
            existing_code = _load_files_from_directory(source_dir, language)
            if existing_code:
                yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.RUNNING,
                                           f"Adapting {len(existing_code)} existing source files to UML...")
                try:
                    current_code = await adapt_code_to_uml(existing_code, optimized_diagram, language)
                    if _is_stopped(pipeline_id): return
                except Exception as e:
                    yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.FAILED, str(e))
                    return
            else:
                existing_code = None

        if not current_code:
            use_integrated = sequence_diagram and sequence_diagram.get("lifelines")
            yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.RUNNING,
                                       "综合生成（类图+时序图）" if use_integrated else "生成代码...")
            try:
                if use_integrated:
                    current_code, _prompt = await generate_integrated_code(
                        optimized_diagram.model_dump(), sequence_diagram, language, component_diagram)
                else:
                    current_code = await generate_code(optimized_diagram, language)
            except Exception as e:
                yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.FAILED, str(e))
                return

        if _is_stopped(pipeline_id): return
        if not current_code:
            logger.error("[Pipeline Stage 3] Code generation returned empty")
            yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.FAILED,
                                       "Code generation failed: LLM returned no valid code files")
            return

        for fname, content in current_code.items():
            pipeline.code_artifacts.append(CodeArtifact(
                language=language, filename=fname, content=content,
            ))
        pipeline.stages[2].result = {"files": list(current_code.keys())}
        _save_generated_files(diagram.name, language, current_code, target_src_dir=source_dir)
        pipeline.stages[2].logs = f"Saved to: {source_dir or f'generated/src/{diagram.name}/{language}/'}"
        logger.info("[Pipeline Stage 3] Code generation done, proceeding to validation")

    # ═══════════════════════════════════════════════════════════════
    # Stage 3b: ReAct Code Validation Gate (resume_pipeline)
    # Only run when new code was generated — skip when reusing cached code
    # ═══════════════════════════════════════════════════════════════
    if current_code and need_code_gen:
        yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.RUNNING,
                                   "ReAct validating generated code...")
        try:
            _ratio = max_change_ratio if source_dir else 0
            _gen_dir = source_dir or os.path.abspath(
                os.path.join(get_settings().uml_dir, "..", "..", "generated", "src"))
            react_result = None
            async for progress in _run_code_validation(
                language=language,
                code_files=current_code,
                task_description="Validate generated code — fix syntax, import, and runtime errors",
                max_rounds=5,
                change_ratio=_ratio,
                design_constraints=pipeline.design_constraints,
                generated_dir=_gen_dir,
            ):
                if "result" in progress:
                    react_result = progress["result"]
                else:
                    pipeline.stages[2].result = {
                        **(pipeline.stages[2].result or {}),
                        "files": list(current_code.keys()),
                        "react_steps": progress["react_steps"],
                    }
                    pipeline.stages[2].logs = f"ReAct round {progress['round']}/{5}..."
                    yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.RUNNING)

            if react_result is None:
                raise RuntimeError("ReAct stream ended without result")

            if react_result["success"]:
                final_code = react_result.get("final_code", {})
                if final_code and final_code != current_code:
                    logger.info("[Stage 3] ReAct fixed code, updating current_code")
                    current_code = final_code
                    for fname, content in current_code.items():
                        for a in pipeline.code_artifacts:
                            if a.filename == fname and a.version == 1:
                                a.content = content
                    _save_generated_files(diagram.name, language, current_code, target_src_dir=source_dir)

                pipeline.stages[2].result = {
                    **(pipeline.stages[2].result or {}),
                    "files": list(current_code.keys()),
                    "react_steps": react_result.get("steps", []),
                    "react_summary": react_result.get("summary", ""),
                }
                pipeline.stages[2].logs = f"Validated: {len(current_code)} files OK"
                yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.SUCCESS)
            else:
                pipeline.stages[2].result = {
                    **(pipeline.stages[2].result or {}),
                    "files": list(current_code.keys()),
                    "react_steps": react_result.get("steps", []),
                    "react_summary": react_result.get("summary", ""),
                    "remaining_issues": react_result.get("remaining_issues", ""),
                }
                _rounds = len(react_result.get("steps", []))
                pipeline.stages[2].logs = f"Validation incomplete after {_rounds} rounds"
                yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.FAILED,
                                           react_result.get("summary", ""))
                return
        except Exception as e:
            logger.exception(f"[Stage 3] ReAct validation crashed: {e}")
            pipeline.stages[2].result = {
                **(pipeline.stages[2].result or {}),
                "react_error": str(e),
            }
            pipeline.stages[2].logs = f"Generated (validation skipped: {e})"
            yield await _update_stage(pipeline, StageName.CODE_GEN, StageStatus.SUCCESS)

    # --- Stage 4: Case Review ---
    if skip_case_review:
        yield await _update_stage(pipeline, StageName.CASE_REVIEW, StageStatus.SUCCESS, "Skipped")
    else:
        yield await _update_stage(pipeline, StageName.CASE_REVIEW, StageStatus.RUNNING,
                                   "请在主画布检视用例，确认后继续")
        yield {
            "event": "request_case_review",
            "pipeline_id": pipeline_id,
            "stage": StageName.CASE_REVIEW.value,
            "message": "请检视并修改用例，完成后点击确认继续",
        }
        return  # Wait for confirm via WebSocket, then resume

    # --- Stage 5: Test Gen ---
    # If test_dir provided and has files → incrementally update based on case review
    # Otherwise → generate from scratch (existing behavior)
    test_cases_data = (pipeline.stages[3].result or {}).get("test_cases", "") if pipeline.stages[3].result else ""
    logger.info(f"[Pipeline Stage 5] test_cases_data present: {bool(test_cases_data)}, length: {len(test_cases_data) if test_cases_data else 0}")

    if test_dir:
        existing_tests = _load_files_from_directory(test_dir, language)
        if existing_tests:
            # Detect missing test modules — source files with no corresponding test
            src_modules = {fname.rsplit(".", 1)[0] for fname in current_code}
            test_modules = set()
            for fname in existing_tests:
                base = fname.replace("test_", "").rsplit(".", 1)[0]
                test_modules.add(base)
            missing_modules = src_modules - test_modules
            if missing_modules:
                missing_note = "\n\n## CRITICAL: Missing test files detected!\n"
                missing_note += "The following source modules have NO corresponding test file.\n"
                missing_note += "Generate complete test files for them:\n"
                for m in sorted(missing_modules):
                    missing_note += f"- test_{m}.py  (tests for {m}.py)\n"
                test_cases_data = (test_cases_data or "") + missing_note
                logger.info(f"[Pipeline Stage 5] Missing test modules detected: {missing_modules}")

            yield await _update_stage(pipeline, StageName.TEST_GEN, StageStatus.RUNNING,
                                       f"Incrementally updating {len(existing_tests)} existing test files...")
            try:
                test_files = await update_tests_incremental(existing_tests, current_code, language, test_cases_data)
                if _is_stopped(pipeline_id): return
                if not test_files:
                    logger.warning("[Pipeline Stage 5] update_tests_incremental returned empty, falling back to full generation")
                else:
                    for fname, content in test_files.items():
                        pipeline.code_artifacts.append(CodeArtifact(
                            language=language, filename=fname, content=content, version=2,
                        ))
                    pipeline.stages[4].result = {"test_files": list(test_files.keys())}
                    _save_generated_files(diagram.name, language, {}, test_files, target_test_dir=test_dir)
                    pipeline.stages[4].logs = f"Updated {len(existing_tests)} existing test files from: {test_dir}"
                    yield await _update_stage(pipeline, StageName.TEST_GEN, StageStatus.SUCCESS)
            except Exception as e:
                logger.warning(f"[Pipeline Stage 5] Incremental update failed: {e}, falling back to full generation")

    if not test_files:
        yield await _update_stage(pipeline, StageName.TEST_GEN, StageStatus.RUNNING)
        try:
            test_files = await generate_tests(
                current_code, language, test_cases_data,
                diagram=optimized_diagram.model_dump() if optimized_diagram else None,
            )
            if _is_stopped(pipeline_id): return
            if not test_files:
                logger.error("[Pipeline Stage 5] generate_tests returned empty — LLM JSON parse likely failed")
                yield await _update_stage(pipeline, StageName.TEST_GEN, StageStatus.FAILED,
                                           "Test generation failed: LLM returned no valid test files")
                return
            for fname, content in test_files.items():
                pipeline.code_artifacts.append(CodeArtifact(
                    language=language, filename=fname, content=content, version=2,
                ))
            pipeline.stages[4].result = {"test_files": list(test_files.keys())}
            _save_generated_files(diagram.name, language, {}, test_files, target_test_dir=test_dir)
            pipeline.stages[4].logs = f"Generated: {'saved to ' + test_dir if test_dir else 'generated/test/' + diagram.name + '/' + language + '/'}"
            yield await _update_stage(pipeline, StageName.TEST_GEN, StageStatus.SUCCESS)
        except Exception as e:
            yield await _update_stage(pipeline, StageName.TEST_GEN, StageStatus.FAILED, str(e))
            return

    # ═══════════════════════════════════════════════════════════════
    # Stage 5b: ReAct Test Code Validation Gate
    # ═══════════════════════════════════════════════════════════════
    if test_files:
        yield await _update_stage(pipeline, StageName.TEST_GEN, StageStatus.RUNNING,
                                   "ReAct validating generated test code...")
        try:
            # Merge source + test files so ReAct's check_imports can resolve
            # cross-module imports (test code imports source modules).
            combined_files = {**current_code, **test_files}
            react_result = None
            async for progress in _run_code_validation(
                language=language,
                code_files=combined_files,
                task_description="Validate generated test code — fix syntax errors, import errors, "
                                 "and ensure all imports match the actual source API. "
                                 "Do NOT modify test logic or assertions. "
                                 "Source files are provided for import resolution ONLY — do NOT modify them.",
                max_rounds=3,
                design_constraints=pipeline.design_constraints,
            ):
                if "result" in progress:
                    react_result = progress["result"]
                else:
                    pipeline.stages[4].result = {
                        **(pipeline.stages[4].result or {}),
                        "test_files": list(test_files.keys()),
                        "react_steps": progress["react_steps"],
                    }
                    pipeline.stages[4].logs = f"ReAct test validation round {progress['round']}/3..."
                    yield await _update_stage(pipeline, StageName.TEST_GEN, StageStatus.RUNNING)

            if react_result is None:
                raise RuntimeError("ReAct test validation stream ended without result")

            # Extract only test files from the combined result (ignore source files)
            def _is_test_file(fname: str) -> bool:
                return fname.startswith("test_") or fname.startswith("test")

            if react_result["success"]:
                if react_result.get("final_code"):
                    new_tests = {k: v for k, v in react_result["final_code"].items() if _is_test_file(k)}
                    if new_tests and new_tests != test_files:
                        logger.info(f"[Stage 5b] ReAct fixed test files, updating: {list(new_tests.keys())}")
                        test_files = new_tests
                        for fname, content in test_files.items():
                            for a in pipeline.code_artifacts:
                                if a.filename == fname and a.version == 2:
                                    a.content = content
                        _save_generated_files(diagram.name, language, {}, test_files, target_test_dir=test_dir)

                pipeline.stages[4].result = {
                    **(pipeline.stages[4].result or {}),
                    "test_files": list(test_files.keys()),
                    "react_steps": react_result.get("steps", []),
                    "react_summary": react_result.get("summary", ""),
                }
                pipeline.stages[4].logs = f"Test validation OK: {len(test_files)} files"
                yield await _update_stage(pipeline, StageName.TEST_GEN, StageStatus.SUCCESS)
            else:
                # ReAct didn't fully succeed — use best-effort result, don't block
                if react_result.get("final_code"):
                    new_tests = {k: v for k, v in react_result["final_code"].items() if _is_test_file(k)}
                    if new_tests:
                        test_files = new_tests
                pipeline.stages[4].result = {
                    **(pipeline.stages[4].result or {}),
                    "test_files": list(test_files.keys()),
                    "react_steps": react_result.get("steps", []),
                    "react_summary": react_result.get("summary", ""),
                    "react_issues": react_result.get("remaining_issues", ""),
                }
                pipeline.stages[4].logs = (
                    f"Test validation: {react_result.get('summary', '')[:120]}"
                    if react_result.get("summary") else "Test validation completed with some issues"
                )
                yield await _update_stage(pipeline, StageName.TEST_GEN, StageStatus.SUCCESS)
        except Exception as e:
            logger.exception(f"[Stage 5b] ReAct test validation crashed: {e}")
            pipeline.stages[4].result = {
                **(pipeline.stages[4].result or {}),
                "test_files": list(test_files.keys()),
                "react_error": str(e),
            }
            pipeline.stages[4].logs = f"Tests generated (validation skipped: {e})"
            yield await _update_stage(pipeline, StageName.TEST_GEN, StageStatus.SUCCESS)

    # --- Stage 6: Test exec + code optimize ---
    async for event in _run_test_and_optimize(pipeline, diagram, language, current_code, test_files,
                                               source_dir=source_dir, test_dir=test_dir):
        yield event
    return
