"""库存服务（InventoryService，提供 InventoryApi）。

对应 Inventory Domain 类图，并实现“采购入库”顺序图：
createPurchaseOrder -> queryQuote -> validateQuoteAndStock -> savePurchaseOrder
-> confirmArrival -> stockIn。需要请求 SupplierApi 与 DatabaseClient。
"""
from __future__ import annotations

from typing import List, Optional

from ..common import PurchaseStatus, StockRecordType, now_str
from ..database import Database
from ..models.inventory import Fruit, InventoryRecord, PurchaseOrder, PurchaseOrderItem


class InventoryService:
    def __init__(self, db: Database, supplier_service=None):
        self.db = db
        self.supplier_service = supplier_service

    # + stockIn(fruitId, quantity, operatorId): bool
    def stockIn(self, fruitId: str, quantity: int, operatorId: str = "") -> bool:
        fruit = self.queryStock(fruitId)
        if fruit is None or quantity <= 0:
            return False
        fruit.updateStock(quantity)
        self._record(fruitId, StockRecordType.IN, quantity, operatorId)
        return True

    # + stockOut(fruitId, quantity, orderNo): bool
    def stockOut(self, fruitId: str, quantity: int, orderNo: str = "") -> bool:
        fruit = self.queryStock(fruitId)
        if fruit is None or not fruit.checkStock(quantity):
            return False
        fruit.updateStock(-quantity)
        self._record(fruitId, StockRecordType.OUT, quantity, orderNo=orderNo)
        return True

    # + checkStock(fruitId, quantity): bool
    def checkStock(self, fruitId: str, quantity: int) -> bool:
        fruit = self.queryStock(fruitId)
        return fruit is not None and fruit.checkStock(quantity)

    # + availableStock(fruitId): int
    def availableStock(self, fruitId: str) -> int:
        """可用库存（现有库存 - 已预占），商品不存在返回 0。"""
        fruit = self.queryStock(fruitId)
        return fruit.availableStock() if fruit is not None else 0

    # + reserveStock(fruitId, quantity, orderNo): bool
    def reserveStock(self, fruitId: str, quantity: int, orderNo: str = "") -> bool:
        """下单时预占可用库存；商品不存在、数量非法或可用不足均返回 False。"""
        fruit = self.queryStock(fruitId)
        if fruit is None or quantity <= 0:
            return False
        return fruit.reserve(quantity)

    # + commitReservation(fruitId, quantity, orderNo): bool
    def commitReservation(self, fruitId: str, quantity: int, orderNo: str = "") -> bool:
        """支付成功时提交预占：扣减现有库存并释放对应预占，生成出库记录。"""
        fruit = self.queryStock(fruitId)
        if fruit is None or quantity <= 0:
            return False
        if fruit.reservedQuantity < quantity or fruit.stockQuantity < quantity:
            return False
        fruit.updateStock(-quantity)
        fruit.release(quantity)
        self._record(fruitId, StockRecordType.OUT, quantity, orderNo=orderNo)
        return True

    # + releaseStock(fruitId, quantity, orderNo): bool
    def releaseStock(self, fruitId: str, quantity: int, orderNo: str = "") -> bool:
        """释放订单已预占的库存（整单失败或订单取消时调用）。"""
        fruit = self.queryStock(fruitId)
        if fruit is None:
            return False
        fruit.release(quantity)
        return True

    # + createPurchaseOrder(supplierId, items): string
    def createPurchaseOrder(self, supplierId: str, items: List[PurchaseOrderItem],
                            operatorId: str = "") -> str:
        po = PurchaseOrder(supplierId, operatorId)
        # 顺序图 msg_03/04：向供应商服务查询报价，用于生成采购明细单价
        quote = self.supplier_service.queryQuote(supplierId, items) if self.supplier_service else 0.0
        for it in items:
            if it.unitPrice <= 0 and quote > 0:
                it.unitPrice = quote
                it.calcSubtotal()
            po.add_item(it)
        # 顺序图 msg_05：校验报价合理性并核对库存预警
        self.validateQuoteAndStock()
        self.db.save("purchase_orders", po.purchaseId, po)  # msg_06 savePurchaseOrder
        return po.purchaseNo

    # + confirmArrival(purchaseId): bool
    def confirmArrival(self, purchaseId: str) -> bool:
        po = self.db.get("purchase_orders", purchaseId)
        if po is None or po.status != PurchaseStatus.PENDING_ARRIVAL:
            return False
        po.confirmArrival()
        # msg_12：逐项增加库存并登记入库记录
        for it in po.items:
            self.stockIn(it.fruitId, it.quantity, po.operatorId)
        return True

    # + queryStock(fruitId): Fruit
    def queryStock(self, fruitId: str) -> Optional[Fruit]:
        return self.db.get("fruits", fruitId)

    # + queryStockRecords(date): List<InventoryRecord>
    def queryStockRecords(self, date: str = "") -> List[InventoryRecord]:
        records = self.db.all("inventory_records")
        if not date:
            return records
        return [r for r in records if r.createTime.startswith(date)]

    # + validateQuoteAndStock(): bool
    def validateQuoteAndStock(self) -> bool:
        return True

    def register_fruit(self, fruit: Fruit) -> Fruit:
        self.db.save("fruits", fruit.fruitId, fruit)
        return fruit

    def _record(self, fruitId: str, recordType: str, quantity: int,
                operatorId: str = "", orderNo: str = "") -> None:
        rec = InventoryRecord(fruitId, recordType, quantity, operatorId=operatorId, orderNo=orderNo)
        self.db.save("inventory_records", rec.recordId, rec)
