"""用户与权限领域模型（对应 User & Permission Domain 类图）。"""
from __future__ import annotations

import hashlib
from typing import List

from ..common import UserStatus, new_id


class User:
    """用户基类（abstract）：封装账号公共信息，密码仅存哈希值。"""

    def __init__(self, username: str, password: str, phone: str = "", email: str = ""):
        self.userId: str = new_id("U")
        self.username: str = username
        self.passwordHash: str = self._hash(password)
        self.phone: str = phone
        self.email: str = email
        self.status: UserStatus = UserStatus.ACTIVE
        self.createTime: str = ""
        self.roles: List["Role"] = []

    @staticmethod
    def _hash(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # + login(username, password): bool
    def login(self, username: str, password: str) -> bool:
        return self.username == username and self.passwordHash == self._hash(password)

    # + logout(): void
    def logout(self) -> None:
        pass

    # + changePassword(oldPwd, newPwd): bool
    def changePassword(self, oldPwd: str, newPwd: str) -> bool:
        if not self.validate():
            return False
        if self.passwordHash != self._hash(oldPwd):
            return False
        self.passwordHash = self._hash(newPwd)
        return True

    # # validate(): bool  (protected)
    def validate(self) -> bool:
        return self.status == UserStatus.ACTIVE


class Customer(User):
    """顾客：会员等级越高折扣越大，购物累计积分可抵扣。"""

    VIP_DISCOUNT = {0: 1.00, 1: 0.98, 2: 0.95, 3: 0.92, 4: 0.88}

    def __init__(self, username: str, password: str, phone: str = "", email: str = ""):
        super().__init__(username, password, phone, email)
        self.vipLevel: int = 0
        self.points: int = 0
        self.balance: float = 0.0

    # + register(): string
    def register(self) -> str:
        return self.userId

    # + recharge(amount: double): bool
    def recharge(self, amount: float) -> bool:
        if amount <= 0:
            return False
        self.balance += amount
        return True

    # + consume(points: int): void
    def consume(self, points: int) -> None:
        self.points = max(0, self.points - points)

    def discount_rate(self) -> float:
        return self.VIP_DISCOUNT.get(self.vipLevel, 1.0)


class Admin(User):
    """管理员/店长：管理水果、订单并生成经营报表。"""

    def __init__(self, username: str, password: str, department: str = "", phone: str = "", email: str = ""):
        super().__init__(username, password, phone, email)
        self.department: str = department

    def manageFruit(self, fruit) -> None:
        pass

    def manageOrder(self, orderId: str) -> None:
        pass

    def generateReport(self, date: str) -> None:
        pass


class Role:
    """角色：顾客、店员、采购员、店长等，角色可挂载多个权限。"""

    def __init__(self, roleId: str, roleName: str, description: str = ""):
        self.roleId = roleId
        self.roleName = roleName
        self.description = description
        self.permissions: List["Permission"] = []

    # + assignPermission(permission: Permission): void
    def assignPermission(self, permission: "Permission") -> None:
        if permission not in self.permissions:
            self.permissions.append(permission)

    # + removePermission(permission: Permission): void
    def removePermission(self, permission: "Permission") -> None:
        if permission in self.permissions:
            self.permissions.remove(permission)


class Permission:
    """权限：定义可执行的操作码，如 manage_fruit、manage_order。"""

    def __init__(self, permId: str, permCode: str, permName: str, description: str = ""):
        self.permId = permId
        self.permCode = permCode
        self.permName = permName
        self.description = description

    # + checkAccess(roleId: string): bool
    def checkAccess(self, roleId: str) -> bool:
        return False
