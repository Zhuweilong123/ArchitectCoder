"""供应商服务（SupplierService，提供 SupplierApi）。

对应 Supplier Domain 类图：报价查询、供应商档案管理与供货评价。
"""
from __future__ import annotations

from typing import List

from ..database import Database
from ..models.supplier import Supplier


class SupplierService:
    def __init__(self, db: Database):
        self.db = db

    # + queryQuote(supplierId, items): double
    def queryQuote(self, supplierId: str, items: List) -> float:
        # 返回报价：依据上一个采购价或供应商成本估算（演示返回固定单价）
        return 3.5

    # + addSupplier(supplier): string
    def addSupplier(self, supplier: Supplier) -> str:
        self.db.save("suppliers", supplier.supplierId, supplier)
        return supplier.supplierId

    # + updateSupplier(supplier): bool
    def updateSupplier(self, supplier: Supplier) -> bool:
        if self.db.get("suppliers", supplier.supplierId) is None:
            return False
        self.db.save("suppliers", supplier.supplierId, supplier)
        return True

    # + evaluateSupplier(supplierId): void
    def evaluateSupplier(self, supplierId: str) -> None:
        supplier = self.db.get("suppliers", supplierId)
        if supplier is not None and supplier.rating < 3.0:
            supplier.status = 0  # 汰汰
