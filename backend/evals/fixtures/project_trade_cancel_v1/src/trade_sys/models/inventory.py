"""库存领域模型（对应 Inventory Domain 类图）。"""
from __future__ import annotations

from typing import List

from ..common import FruitStatus, PurchaseStatus, StockRecordType, new_id, now_str


class FruitCategory:
    """水果分类：如苹果、柑橘、热带水果、浆果等。"""

    def __init__(self, categoryId: str, categoryName: str, description: str = ""):
        self.categoryId = categoryId
        self.categoryName = categoryName
        self.description = description
        self.fruits: List["Fruit"] = []

    # + addCategory(): void
    def addCategory(self) -> None:
        pass

    # + updateCategory(): void
    def updateCategory(self) -> None:
        pass


class Fruit:
    """水果商品：库存低于 safetyStock 时触发补货提醒；按保质期管理。"""

    def __init__(self, name: str, categoryId: str, unit: str, price: float,
                 costPrice: float = 0.0, stockQuantity: int = 0, safetyStock: int = 0,
                 shelfLife: int = 0, origin: str = ""):
        self.fruitId = new_id("F")
        self.name = name
        self.categoryId = categoryId
        self.unit = unit
        self.price = price
        self.costPrice = costPrice
        self.stockQuantity = stockQuantity
        self.reservedQuantity = 0
        self.safetyStock = safetyStock
        self.shelfLife = shelfLife
        self.origin = origin
        self.status = FruitStatus.ON_SALE
        self.records: List["InventoryRecord"] = []

    # + updatePrice(newPrice): void
    def updatePrice(self, newPrice: float) -> None:
        self.price = newPrice

    # + updateStock(quantity): void
    def updateStock(self, quantity: int) -> None:
        self.stockQuantity += quantity

    # + checkStock(quantity): bool
    def checkStock(self, quantity: int) -> bool:
        return self.stockQuantity >= quantity

    # + availableStock(): int
    def availableStock(self) -> int:
        """可用库存 = 现有库存 - 已预占库存。"""
        return self.stockQuantity - self.reservedQuantity

    # + reserve(quantity): bool
    def reserve(self, quantity: int) -> bool:
        """预占可用库存；数量非法或可用库存不足时返回 False（不产生负可用）。"""
        if quantity <= 0 or self.availableStock() < quantity:
            return False
        self.reservedQuantity += quantity
        return True

    # + release(quantity): void
    def release(self, quantity: int) -> None:
        """释放已预占库存，下限为 0。"""
        self.reservedQuantity = max(0, self.reservedQuantity - quantity)

    def need_restock(self) -> bool:
        return self.stockQuantity <= self.safetyStock


class InventoryRecord:
    """出入库记录：type 为 IN/OUT，与采购单或销售订单关联，支持追溯。"""

    def __init__(self, fruitId: str, recordType: str, quantity: int,
                 operatorId: str = "", orderNo: str = "", remark: str = ""):
        self.recordId = new_id("IR")
        self.fruitId = fruitId
        self.type = recordType
        self.quantity = quantity
        self.operatorId = operatorId
        self.orderNo = orderNo
        self.createTime = now_str()
        self.remark = remark

    # + recordIn(): void
    def recordIn(self) -> None:
        self.type = StockRecordType.IN

    # + recordOut(): void
    def recordOut(self) -> None:
        self.type = StockRecordType.OUT

    # + queryByFruit(fruitId): List<InventoryRecord>
    def queryByFruit(self, fruitId: str) -> List["InventoryRecord"]:
        return [self] if self.fruitId == fruitId else []


class PurchaseOrder:
    """采购单：状态 待到货->已到货/已取消，到货后自动入库。"""

    def __init__(self, supplierId: str, operatorId: str = ""):
        self.purchaseId = new_id("PO")
        self.purchaseNo = "PNO" + self.purchaseId[2:]
        self.supplierId = supplierId
        self.totalAmount = 0.0
        self.status = PurchaseStatus.PENDING_ARRIVAL
        self.createTime = now_str()
        self.arriveTime = ""
        self.operatorId = operatorId
        self.items: List["PurchaseOrderItem"] = []

    def add_item(self, item: "PurchaseOrderItem") -> None:
        self.items.append(item)
        self.totalAmount = round(sum(i.subtotal for i in self.items), 2)

    # + createPurchaseOrder(): void
    def createPurchaseOrder(self) -> None:
        self.status = PurchaseStatus.PENDING_ARRIVAL

    # + confirmArrival(): void
    def confirmArrival(self) -> None:
        self.status = PurchaseStatus.ARRIVED
        self.arriveTime = now_str()

    # + cancelPurchase(): bool
    def cancelPurchase(self) -> bool:
        if self.status == PurchaseStatus.PENDING_ARRIVAL:
            self.status = PurchaseStatus.CANCELLED
            return True
        return False


class PurchaseOrderItem:
    """采购明细：记录采购单价用于成本核算。"""

    def __init__(self, purchaseId: str, fruitId: str, quantity: int, unitPrice: float):
        self.itemId = new_id("POI")
        self.purchaseId = purchaseId
        self.fruitId = fruitId
        self.quantity = quantity
        self.unitPrice = unitPrice
        self.subtotal = 0.0
        self.calcSubtotal()

    # + calcSubtotal(): double
    def calcSubtotal(self) -> float:
        self.subtotal = round(self.unitPrice * self.quantity, 2)
        return self.subtotal
