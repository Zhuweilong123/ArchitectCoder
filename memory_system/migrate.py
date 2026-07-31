#!/usr/bin/env python3
"""
JSON → SQLite 迁移脚本

将旧版 JSON 文件存储的记忆迁移到新的 SQLite 数据库.

用法:
    cd memory_system
    python migrate.py --json-dir ./demo_output/memories --db ./memories.db

    # 或者传入具体的项目名
    python migrate.py --json-dir ./memories --db ./memories.db --project blog_system
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import List

# 确保可以导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_system import MemoryManager, MemoryEntry, MemoryType

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("migrate")


def load_json_memories(json_file: Path) -> List[dict]:
    """加载旧 JSON 文件."""
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.warning(f"  [WARN] {json_file.name}: not a list, skipping")
            return []
        return data
    except (json.JSONDecodeError, IOError) as exc:
        logger.warning(f"  [WARN] {json_file.name}: {exc}")
        return []


def migrate_project(manager: MemoryManager, json_file: Path) -> int:
    """
    迁移单个项目的 JSON 记忆到 SQLite.

    字段映射:
      - content → summary
      - context → original_text
      - importance → importance_score
      - timestamp → created_at
      - tags / memory_type / source / user_feedback → 直接映射
      - metadata → 新增 (保存旧版 context 等信息)
    """
    project_name = json_file.stem
    data = load_json_memories(json_file)
    if not data:
        return 0

    count = 0
    skipped = 0
    for item in data:
        try:
            memory_type = MemoryType(item.get("memory_type", "insight"))
        except (ValueError, KeyError):
            logger.warning(f"  [SKIP] Invalid memory_type in {project_name}")
            skipped += 1
            continue

        entry = MemoryEntry(
            id=item.get("id", ""),  # 保留旧 ID
            project_id=item.get("project_id", project_name),
            memory_type=memory_type,
            summary=item.get("content", item.get("summary", "")),
            original_text=item.get("context", item.get("original_text", "")),
            metadata={
                "migrated_from": "json_v1",
                "original_context": item.get("context", ""),
                "original_timestamp": item.get("timestamp", ""),
            },
            importance_score=float(item.get("importance", 0.5)),
            access_count=0,
            last_accessed_at=None,
            created_at=item.get("timestamp", item.get("created_at", "")),
            tags=item.get("tags", []),
            source=item.get("source", ""),
            user_feedback=item.get("user_feedback"),
            is_pinned=False,
        )

        manager.db.add(entry)
        count += 1

    logger.info(
        f"  [OK] {project_name}: migrated {count} memories"
        + (f" (skipped {skipped})" if skipped else "")
    )
    return count


def main():
    parser = argparse.ArgumentParser(description="Migrate JSON memories to SQLite")
    parser.add_argument(
        "--json-dir", required=True,
        help="Directory containing old JSON memory files (*.json)"
    )
    parser.add_argument(
        "--db", default="./memories.db",
        help="Target SQLite database path (default: ./memories.db)"
    )
    parser.add_argument(
        "--project", default=None,
        help="Migrate only a specific project (default: all .json files)"
    )
    args = parser.parse_args()

    json_dir = Path(args.json_dir)
    if not json_dir.is_dir():
        logger.error(f"JSON directory not found: {json_dir}")
        sys.exit(1)

    manager = MemoryManager(db_path=args.db)
    total = 0

    if args.project:
        json_file = json_dir / f"{args.project}.json"
        if json_file.exists():
            total += migrate_project(manager, json_file)
        else:
            logger.error(f"Project file not found: {json_file}")
            sys.exit(1)
    else:
        json_files = sorted(json_dir.glob("*.json"))
        if not json_files:
            logger.warning(f"No .json files found in {json_dir}")
            sys.exit(0)

        logger.info(f"Found {len(json_files)} JSON file(s) in {json_dir}")
        for jf in json_files:
            total += migrate_project(manager, jf)

    manager.close()
    logger.info(f"\nDone. Total memories migrated: {total}")
    logger.info(f"Database: {Path(args.db).resolve()}")


if __name__ == "__main__":
    main()
