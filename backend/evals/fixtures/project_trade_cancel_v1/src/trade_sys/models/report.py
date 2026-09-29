"""报表领域模型（对应 Report Domain 类图）。"""
from __future__ import annotations

from typing import List

from ..common import new_id, now_str


class SalesReport:
    """销售报表：按日/月汇总销售额、订单量、毛利与热销水果排行。"""

    def __init__(self, reportDate: str):
        self.reportId = new_id("SR")
        self.reportDate = reportDate
        self.totalSales = 0.0
        self.totalOrders = 0
        self.totalProfit = 0.0
        self.topFruits: List[str] = []

    # + generateDailyReport(date): SalesReport
    def generateDailyReport(self, date: str) -> "SalesReport":
        return self

    # + aggregateByCategory(): void
    def aggregateByCategory(self) -> None:
        pass


class InventoryReport:
    """库存报表：库存总值、低库存预警、临期水果与损耗统计。"""

    def __init__(self, reportDate: str = ""):
        self.reportId = new_id("IVR")
        self.reportDate = reportDate or now_str()
        self.totalStockValue = 0.0
        self.lowStockItems: List[str] = []
        self.expiredSoonItems: List[str] = []
        self.wasteQuantity = 0.0

    # + generateStockReport(): InventoryReport
    def generateStockReport(self) -> "InventoryReport":
        return self

    # + generateWasteReport(): InventoryReport
    def generateWasteReport(self) -> "InventoryReport":
        return self
