"""SubmitUmlReviewTool 单元测试（async future 等待闭环）。"""
import asyncio
import json

from app.agent_base.tools.review import (
    ReviewManager, SubmitUmlReviewTool,
)


async def _run_with_resolve(mgr, tool, params, resolve_value, delay=0.05):
    """在一个 task 里跑工具（会 await future），主协程稍后 resolve。"""
    task = asyncio.create_task(tool._execute(params))
    await asyncio.sleep(delay)  # 让 submit 完成、future 创建
    mgr.resolve(0, resolve_value)
    return await task


def test_submit_uml_review_accept():
    mgr = ReviewManager()
    tool = SubmitUmlReviewTool(manager=mgr, timeout=5)

    async def _scenario():
        return await _run_with_resolve(
            mgr, tool,
            {"diagrams_json": json.dumps([{"name": "A"}]), "summary": "add class"},
            json.dumps({"decision": "accept", "feedback": ""}),
        )

    result = asyncio.run(_scenario())
    assert "accept" in result


def test_submit_uml_review_reject_with_feedback():
    mgr = ReviewManager()
    tool = SubmitUmlReviewTool(manager=mgr, timeout=5)

    async def _scenario():
        return await _run_with_resolve(
            mgr, tool,
            {"diagrams_json": json.dumps([{"name": "A"}])},
            json.dumps({"decision": "reject", "feedback": "关联关系改错了"}, ensure_ascii=False),
        )

    result = asyncio.run(_scenario())
    assert "reject" in result
    assert "关联关系改错了" in result


def test_submit_uml_review_metadata():
    """submit 后 metadata 承载 diagrams / original_diagrams，供前端 diff 使用。"""
    mgr = ReviewManager()
    tool = SubmitUmlReviewTool(manager=mgr, timeout=5)

    async def _scenario():
        task = asyncio.create_task(tool._execute({
            "diagrams_json": json.dumps([{"name": "new"}]),
            "original_diagrams_json": json.dumps([{"name": "old"}]),
        }))
        await asyncio.sleep(0.05)
        pending = mgr.get_pending()
        mgr.resolve(0, json.dumps({"decision": "accept", "feedback": ""}))
        await task
        return pending

    pending = asyncio.run(_scenario())
    assert len(pending) == 1
    assert pending[0]["review_type"] == "uml_diff"
    assert pending[0]["metadata"]["diagrams"] == [{"name": "new"}]
    assert pending[0]["metadata"]["original_diagrams"] == [{"name": "old"}]


def test_submit_uml_review_resolves_workspace_relative_project_file(tmp_path):
    project_root = tmp_path / "project"
    design_dir = project_root / "design"
    design_dir.mkdir(parents=True)
    project_file = design_dir / "example.umlproj"
    project_file.write_text(json.dumps({
        "version": "1.0",
        "name": "example",
        "diagrams": [{
            "name": "Architecture",
            "diagram_type": "component",
            "components": [{"id": "component_a", "name": "A"}],
            "comp_relations": [],
        }],
    }), encoding="utf-8")

    mgr = ReviewManager()
    tool = SubmitUmlReviewTool(
        manager=mgr,
        timeout=5,
        project_file=str(project_file),
        workspace_root=str(project_root),
    )

    async def _scenario():
        mgr.baseline = [{
            "name": "Architecture",
            "diagram_type": "component",
            "components": [{"id": "component_a", "name": "OldA"}],
            "comp_relations": [],
        }]
        task = asyncio.create_task(tool._execute({
            "project_file": "design/example.umlproj",
            "summary": "rename component",
        }))
        await asyncio.sleep(0.05)
        pending = mgr.get_pending()
        mgr.resolve(0, json.dumps({"decision": "accept", "feedback": ""}))
        await task
        return pending

    pending = asyncio.run(_scenario())
    assert pending[0]["metadata"]["diagrams"][0]["name"] == "Architecture"
    assert pending[0]["metadata"]["changed_diagrams"]


def test_review_id_is_not_reused_after_reset():
    mgr = ReviewManager()
    first = mgr.submit("code", title="first")
    mgr.reset()
    second = mgr.submit("code", title="second")

    assert second.id != first.id
    assert mgr.resolve(first.id, "stale") is False
    assert mgr.resolve(second.token, "fresh") is True


def test_uml_review_auto_approval_stub_keeps_tool_continuous():
    mgr = ReviewManager(auto_approve_reviews=True)
    tool = SubmitUmlReviewTool(manager=mgr, timeout=1)

    result = asyncio.run(tool._execute({
        "diagrams_json": json.dumps([{"name": "accepted"}]),
        "summary": "evaluation UML change",
    }))

    assert json.loads(result)["decision"] == "accept"
    assert not mgr.has_pending()
    assert [event["event"] for event in mgr.approval_events] == [
        "review_requested", "review_response",
    ]
    assert mgr.approval_events[-1]["approval_mode"] == "auto_stub"
