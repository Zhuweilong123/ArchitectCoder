"""报表服务（ReportService，提供 ReportApi）。

对应 Report Domain 类图，并实现“Sales Report Generation Flow”顺序图：
generateDailySalesReport -> queryOrders + queryStockRecords -> aggregateByCategory
-> saveReport。需要请求 SalesApi、InventoryApi 与 DatabaseClient。
"""
from __future__ import annotations

from collections import defaultdict
from typing import List

from ..common import StockRecordType
from ..database import Database
from ..models.report import InventoryReport, SalesReport


class ReportService:
    def __init__(self, db: Database, sales_service=None, inventory_service=None):
        self.db = db
        self.sales_service = sales_service
        self.inventory_service = inventory_service

    # + generateDailySalesReport(date): SalesReport
    def generateDailySalesReport(self, date: str) -> SalesReport:
        report = SalesReport(date)
        # msg_02：查询该日期全部订单
        orders = self.sales_service.queryOrders(date) if self.sales_service else []
        # msg_04：查询当日出入库记录用于成本核算
        records = self.inventory_service.queryStockRecords(date) if self.inventory_service else []
        # msg_06：按品类聚合销售额、销量与毛利
        self._aggregate(report, orders, records)
        self.db.save("reports", report.reportId, report)  # msg_07
        return report

    # + generateMonthlyReport(month): SalesReport
    def generateMonthlyReport(self, month: str) -> SalesReport:
        report = SalesReport(month)
        orders = self.sales_service.queryOrders() if self.sales_service else []
        orders = [o for o in orders if o.createTime.startswith(month)]
        records = self.inventory_service.queryStockRecords() if self.inventory_service else []
        records = [r for r in records if r.createTime.startswith(month)]
        self._aggregate(report, orders, records)
        self.db.save("reports", report.reportId, report)
        return report

    # + generateInventoryReport(): InventoryReport
    def generateInventoryReport(self) -> InventoryReport:
        report = InventoryReport()
        for fruit in self.db.all("fruits"):
            report.totalStockValue += fruit.stockQuantity * fruit.costPrice
            if fruit.need_restock():
                report.lowStockItems.append(fruit.name)
        report.totalStockValue = round(report.totalStockValue, 2)
        self.db.save("reports", report.reportId, report)
        return report

    # + exportExcel(reportId): string
    def exportExcel(self, reportId: str) -> str:
        report = self.db.get("reports", reportId)
        if report is None:
            return ""
        return f"{report.reportId}.xlsx"

    # + aggregateByCategory(): void
    def aggregateByCategory(self) -> None:
        pass

    def _aggregate(self, report: SalesReport, orders: List, records: List) -> None:
        sales = 0.0
        count = 0
        fruit_sales = defaultdict(float)
        cost = 0.0
        for o in orders:
            sales += o.payAmount
            count += 1
            for it in o.items:
                fruit_sales[it.fruitName] += it.subtotal
        # 成本：当日出库量 x 商品成本价
        for rec in records:
            if rec.type == StockRecordType.OUT:
                fruit = self.db.get("fruits", rec.fruitId)
                if fruit is not None:
                    cost += rec.quantity * fruit.costPrice
        profit = sales - cost
        report.totalSales = round(sales, 2)
        report.totalOrders = count
        report.totalProfit = round(profit, 2)
        report.topFruits = [name for name, _ in sorted(fruit_sales.items(), key=lambda kv: kv[1], reverse=True)]
