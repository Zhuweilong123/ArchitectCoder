#!/usr/bin/env python3
"""
Memory System v2 独立演示

模拟完整的 LLM 跨会话记忆流程:
  1. 第一轮 LLM 交互 -> remember() 提取记忆
  2. 第二轮 LLM 交互前 -> recall() 检索 + inject_memories() 注入
  3. reinforce() 强化被使用的记忆
  4. maintenance() 衰减 + 淘汰
  5. 展示完整的"记忆 → 检索 → 注入 → 强化 → 维护"闭环

运行:
    cd memory_system
    python demo.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_system import (
    MemoryManager, MemoryConfig, MemoryType, is_jieba_available,
)


# ---------------------------------------------------------------------------
# 模拟 LLM 提取函数
# ---------------------------------------------------------------------------

async def mock_extract_fn(prompt: str) -> str:
    """模拟: 从 LLM 交互中提取结构化记忆."""
    if "优化类图" in prompt and "提高可扩展性" in prompt:
        return json.dumps([
            {
                "memory_type": "preference",
                "summary": "用户偏好使用组合模式而非继承来扩展系统功能",
                "original_text": "在优化 Blog 系统类图时, 用户明确要求提高可扩展性, "
                                "LLM 采用了 CQRS 分离读写服务, 引入 Repository 接口, "
                                "并使用组合模式替代继承链",
                "tags": ["设计模式", "组合优于继承", "类图"],
                "importance": 0.8,
            },
            {
                "memory_type": "decision",
                "summary": "BlogService 采用 CQRS 分离为 BlogWriteService 和 BlogReadService",
                "original_text": "LLM 将 BlogService 拆分为写服务和读服务, "
                                "引入 PostRepository 接口实现依赖倒置",
                "tags": ["CQRS", "架构", "读写分离"],
                "importance": 0.9,
            },
            {
                "memory_type": "insight",
                "summary": "项目领域模型倾向贫血模型, Service 层放业务逻辑",
                "original_text": "从类图设计看, 该项目的 Entity 只包含数据和 getter/setter, "
                                "业务逻辑集中在 Service 层, 符合贫血模型风格",
                "tags": ["贫血模型", "领域驱动", "架构风格"],
                "importance": 0.6,
            },
        ])
    elif "时序图" in prompt and "认证流程" in prompt:
        return json.dumps([
            {
                "memory_type": "decision",
                "summary": "认证流程使用 JWT Token + Refresh Token 双令牌机制",
                "original_text": "用户请求优化登录时序图后, LLM 添加了 Access Token + "
                                "Refresh Token 流程, Token 过期自动刷新",
                "tags": ["JWT", "认证", "时序图", "安全"],
                "importance": 0.9,
            },
            {
                "memory_type": "rejection",
                "summary": "不要使用 Session 认证, 用户在第 2 轮优化时拒绝了该方案",
                "original_text": "在早期的认证流程设计中, LLM 提出了基于 Session 的方案, "
                                "但用户明确拒绝了, 选择了 JWT 方案",
                "tags": ["Session", "认证", "已拒绝"],
                "importance": 0.85,
            },
        ])
    elif "全局优化" in prompt and "一致性" in prompt:
        return json.dumps([
            {
                "memory_type": "convention",
                "summary": "项目统一使用 MVC 三层架构, Controller 不直接访问 DAO",
                "original_text": "全局一致性检查发现, 所有图表都应遵循 MVC 分层, "
                                "Controller 通过 Service 访问数据, 不直接调用 DAO",
                "tags": ["MVC", "架构规范", "分层"],
                "importance": 0.75,
            },
            {
                "memory_type": "preference",
                "summary": "命名规范遵循 Python PEP 8, 类名大驼峰, 方法名蛇形",
                "original_text": "跨图检查时发现部分类名不一致, 统一为 PEP 8 规范",
                "tags": ["命名规范", "PEP8", "Python"],
                "importance": 0.5,
            },
        ])
    else:
        return json.dumps([
            {
                "memory_type": "insight",
                "summary": f"LLM 优化了 UML 设计, 改动集中在类关系调整",
                "original_text": f"本次交互中 LLM 对 UML 进行了优化",
                "tags": ["优化", "类关系"],
                "importance": 0.4,
            }
        ])


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

async def main():
    print("=" * 70)
    print("  Memory System v2 Demo — SQLite + FTS5 + jieba 记忆系统")
    print("=" * 70)
    print(f"  jieba: {'enabled' if is_jieba_available() else 'fallback to bigram'}")
    print()

    # 初始化
    storage_dir = os.path.join(os.path.dirname(__file__), "demo_output")
    os.makedirs(storage_dir, exist_ok=True)
    db_path = os.path.join(storage_dir, "memories.db")

    # 清理旧数据库, 确保演示从干净状态开始
    if os.path.exists(db_path):
        os.remove(db_path)

    config = MemoryConfig(
        db_path=db_path,
        max_entries_per_project=20,
        enable_bm25=True,
        bm25_top_k=10,
    )
    manager = MemoryManager(config=config)
    project = "blog_system"

    print(f"[DB]  {db_path}")
    print(f"[Project] {project}")
    print()

    # ==================================================================
    # 会话 1: 优化类图
    # ==================================================================
    print("─" * 70)
    print("  Session 1: 用户请求优化类图设计")
    print("─" * 70)

    user_query_1 = "请优化 Blog 系统的类图设计，提高可扩展性"
    print(f"\n[User] {user_query_1}")

    llm_response_1 = """
    已优化类图:
    - 将 BlogService 拆分为 BlogWriteService 和 BlogReadService (CQRS)
    - 引入 PostRepository 接口 (依赖倒置)
    - 使用组合模式替代继承链
    """
    print(f"[LLM]  优化完成, 变更了 3 个类")

    print("\n[Extract] 提取记忆...")
    entries_1 = await manager.remember(
        project_id=project,
        context="用户请求优化 Blog 系统类图, 提高可扩展性",
        llm_call_type="optimize",
        user_input=user_query_1,
        llm_output=llm_response_1,
        user_feedback="accepted",
        extract_fn=mock_extract_fn,
    )

    for e in entries_1:
        print(f"   [+] [{e.memory_type.value}] {e.summary[:60]}...")
    print(f"   -> 共提取 {len(entries_1)} 条记忆")
    print()

    # ==================================================================
    # 会话 2: 优化时序图
    # ==================================================================
    print("─" * 70)
    print("  Session 2: 用户请求优化时序图 (认证流程)")
    print("─" * 70)

    user_query_2 = "请优化用户登录的时序图，添加 JWT 认证流程"
    print(f"\n[User] {user_query_2}")

    # 检索相关记忆
    print("\n[Search] BM25 检索相关记忆...")
    results = await manager.recall(
        project_id=project,
        query=user_query_2,
        top_k=5,
        max_tokens=800,
    )

    for i, rr in enumerate(results, 1):
        print(
            f"   {i}. [{rr.entry.memory_type.value}] {rr.entry.summary[:60]}... "
            f"(score: {rr.score:.4f})"
        )

    # 注入 system prompt
    system_prompt = (
        "你是一个 UML 设计专家, 擅长时序图分析和优化。"
        "请根据用户需求生成优化的时序图 JSON。"
    )
    enriched_prompt = manager.inject_memories(system_prompt, results)
    print(f"\n[Inject] 注入记忆后 System Prompt 增长: {len(system_prompt)} → {len(enriched_prompt)} chars")

    # 强化被使用的记忆
    reinforced_count = manager.reinforce(results)
    print(f"[Reinforce] 强化了 {reinforced_count} 条被检索使用的记忆")

    # 模拟 LLM 响应
    llm_response_2 = "已优化时序图: 添加 Access Token + Refresh Token 流程"
    print(f"\n[LLM]  {llm_response_2}")

    # 提取新记忆
    print("\n[Extract] 提取记忆...")
    entries_2 = await manager.remember(
        project_id=project,
        context="用户请求优化登录时序图, 添加 JWT 认证流程",
        llm_call_type="optimize",
        user_input=user_query_2,
        llm_output=llm_response_2,
        user_feedback="accepted",
        extract_fn=mock_extract_fn,
    )

    for e in entries_2:
        print(f"   [+] [{e.memory_type.value}] {e.summary[:60]}...")
    print(f"   -> 共提取 {len(entries_2)} 条记忆")
    print()

    # ==================================================================
    # 会话 3: 全局优化
    # ==================================================================
    print("─" * 70)
    print("  Session 3: 用户请求全局优化 (跨图一致性校验)")
    print("─" * 70)

    user_query_3 = "请对类图+时序图+组件图进行全局优化，确保三者一致"
    print(f"\n[User] {user_query_3}")

    print("\n[Search] BM25 检索相关记忆...")
    results_3 = await manager.recall(
        project_id=project,
        query=user_query_3,
        top_k=5,
    )

    for i, rr in enumerate(results_3, 1):
        print(
            f"   {i}. [{rr.entry.memory_type.value}] {rr.entry.summary[:60]}... "
            f"(score: {rr.score:.4f})"
        )
    print(f"   -> 共检索到 {len(results_3)} 条相关记忆")

    # 注入
    enriched_3 = manager.inject_memories(
        "你是 UML 全局优化专家，请对多张图进行交叉验证和协同优化。",
        results_3,
    )
    memory_section = enriched_3.split("## 项目历史记忆")[1] if "## 项目历史记忆" in enriched_3 else ""
    if memory_section:
        print(f"\n[Inject] 注入的记忆片段:")
        print(memory_section)

    # 强化
    manager.reinforce(results_3)

    # 提取
    await manager.remember(
        project_id=project,
        context="用户请求全局优化: 类图+时序图+组件图一致性校验",
        llm_call_type="optimize",
        user_input=user_query_3,
        llm_output="全局一致性校验完成, 修正了 5 处不一致",
        user_feedback="accepted",
        extract_fn=mock_extract_fn,
    )
    print()

    # ==================================================================
    # 记忆库状态
    # ==================================================================
    print("─" * 70)
    print("  Memory Store 状态")
    print("─" * 70)

    stats = await manager.stats(project)
    print(f"\n[Stats] 项目 '{project}':")
    print(f"   总记忆数: {stats['total_memories']}")
    print(f"   按类型:   {json.dumps(stats['by_type'], ensure_ascii=False)}")
    print(f"   FTS5 文档: {stats['fts_docs']}")
    print(f"   平均重要性: {stats['avg_importance']}")

    all_memories = await manager.list_memories(project)
    print(f"\n[Note] 所有记忆:")
    for i, e in enumerate(all_memories, 1):
        print(f"   {i}. [{e.memory_type.value}] {e.summary}")
        print(f"      tags: {e.tags} | imp: {e.importance_score:.2f} | "
              f"accessed: {e.access_count} | age: {e.age_days:.1f}d")

    # ==================================================================
    # 生命周期管理
    # ==================================================================
    print()
    print("─" * 70)
    print("  生命周期管理: 衰减 + 淘汰")
    print("─" * 70)

    # Pin 一条重要记忆
    if all_memories:
        important = all_memories[0]
        manager.pin(important.id, project)
        print(f"\n[Pin] 固定记忆: [{important.memory_type.value}] {important.summary[:50]}...")

    # 执行维护
    result = manager.maintenance(project)
    print(f"[Maintenance] 衰减: {result['decayed']} 条, 淘汰: {result['pruned']} 条")

    stats_after = await manager.stats(project)
    print(f"[Stats] 维护后记忆数: {stats_after['total_memories']}")

    # ==================================================================
    # forget 测试
    # ==================================================================
    print()
    print("─" * 70)
    print("  测试 forget: 删除一条记忆")
    print("─" * 70)

    remaining = await manager.list_memories(project)
    if remaining:
        target = remaining[-1]  # 最旧的一条
        print(f"\n[Delete] 删除: [{target.memory_type.value}] {target.summary[:40]}...")
        ok = await manager.forget(project, target.id)
        print(f"   {'[OK] Deleted' if ok else '[FAIL] Delete failed'}")

        final_stats = await manager.stats(project)
        print(f"   删除后总记忆数: {final_stats['total_memories']}")

    # ==================================================================
    # Done
    # ==================================================================
    manager.close()
    print()
    print("=" * 70)
    print("  [OK] Demo complete!")
    print(f"  [DB]  {os.path.abspath(db_path)}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
