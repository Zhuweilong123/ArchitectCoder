"""内存数据库客户端（DatabaseClient）。

对应组件图中的 Database 组件，为各服务提供数据持久化能力（DatabaseClient 接口）。
本实现使用内存字典模拟，便于直接运行演示；真实部署时可替换为 ORM/关系数据库。
"""
from __future__ import annotations

from typing import Dict, List


class Database:
    """DatabaseClient 的内存实现，按表组织，主键为业务 ID。"""

    _TABLES = (
        "users", "roles", "permissions",
        "fruits", "categories", "inventory_records", "purchase_orders",
        "orders", "carts", "coupons", "payments",
        "reports", "suppliers",
    )

    def __init__(self) -> None:
        for table in self._TABLES:
            setattr(self, table, {})
        self.user_roles: Dict[str, List[str]] = {}
        self.role_permissions: Dict[str, List[str]] = {}

    def save(self, table: str, key: str, value: object) -> object:
        getattr(self, table)[key] = value
        return value

    def get(self, table: str, key: str):
        return getattr(self, table).get(key)

    def all(self, table: str) -> List[object]:
        return list(getattr(self, table).values())

    def find(self, table: str, predicate) -> List[object]:
        return [row for row in getattr(self, table).values() if predicate(row)]
