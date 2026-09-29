"""用户服务（UserService，提供 UserApi）。

对应 User & Permission Domain 类图：负责账号认证、注册、角色分配与权限校验。
"""
from __future__ import annotations

from typing import Optional

from ..database import Database
from ..models.user import Admin, Customer, Permission, Role, User


class UserService:
    def __init__(self, db: Database):
        self.db = db

    # + login(username, password): bool
    def login(self, username: str, password: str) -> bool:
        user = self._find_by_username(username)
        return user is not None and user.login(username, password)

    # + register(customer: Customer): string
    def register(self, customer: Customer) -> str:
        self.db.save("users", customer.userId, customer)
        return customer.userId

    # + assignRole(userId, roleId): void
    def assignRole(self, userId: str, roleId: str) -> None:
        self.db.user_roles.setdefault(userId, [])
        if roleId not in self.db.user_roles[userId]:
            self.db.user_roles[userId].append(roleId)

    # + checkPermission(userId, permCode): bool
    def checkPermission(self, userId: str, permCode: str) -> bool:
        role_ids = self.db.user_roles.get(userId, [])
        for rid in role_ids:
            for pid in self.db.role_permissions.get(rid, []):
                perm = self.db.get("permissions", pid)
                if perm is not None and perm.permCode == permCode:
                    return True
        return False

    def _find_by_username(self, username: str) -> Optional[User]:
        for user in self.db.all("users"):
            if user.username == username:
                return user
        return None
