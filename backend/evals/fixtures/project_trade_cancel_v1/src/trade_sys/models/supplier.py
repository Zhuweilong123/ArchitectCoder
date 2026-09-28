"""供应商领域模型（对应 Supplier Domain 类图）。"""
from __future__ import annotations

from ..common import new_id


class Supplier:
    """供应商档案：记录供货品质评分，评分低者进入汰汰名单。"""

    def __init__(self, supplierName: str, contactPerson: str = "", phone: str = "",
                 address: str = "", rating: float = 5.0):
        self.supplierId = new_id("S")
        self.supplierName = supplierName
        self.contactPerson = contactPerson
        self.phone = phone
        self.address = address
        self.rating = rating
        self.status = 1

    # + addSupplier(): void
    def addSupplier(self) -> None:
        pass

    # + updateSupplier(): void
    def updateSupplier(self) -> None:
        pass

    # + queryQuote(items): double
    def queryQuote(self, items) -> float:
        return 0.0
