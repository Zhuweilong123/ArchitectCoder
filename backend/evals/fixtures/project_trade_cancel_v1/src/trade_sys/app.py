"""应用组装与组装根（composition root）。

按组件图连线关系装配各服务：
- WebFrontend 依赖 Sales/User/Report 组件
- SalesService 依赖 Inventory/User/Database 组件
- InventoryService 依赖 Supplier/Database 组件
- ReportService 依赖 Sales/Inventory/Database 组件
- 各服务均依赖 Database 组件（同一 DatabaseClient）
"""
from __future__ import annotations

from .database import Database
from .services import (
    InventoryService, ReportService, SalesService, SupplierService, UserService,
)
from .web import (
    AdminDashboardView, FruitCatalogView, LoginView, OrderView, ShoppingCartView,
)


class FruitSalesApp:
    """整个水果销售系统的组装与对外入口（充当 WebFrontend 应用容器）。"""

    def __init__(self) -> None:
        # Database 组件：所有服务共享同一个 DatabaseClient
        self.db = Database()

        # 叶子服务 -> 依赖叶子服务的服务
        self.user_service = UserService(self.db)                 # UserService -> Database
        self.supplier_service = SupplierService(self.db)         # SupplierService -> Database
        self.inventory_service = InventoryService(self.db, self.supplier_service)  # -> Supplier, Database
        self.sales_service = SalesService(self.db, self.inventory_service, self.user_service)  # -> Inventory, User, Database
        self.report_service = ReportService(self.db, self.sales_service, self.inventory_service)  # -> Sales, Inventory, Database

        # WebFrontend 视图
        self.login_view = LoginView(self.user_service)
        self.catalog_view = FruitCatalogView(self.sales_service, self.inventory_service)
        self.cart_view = ShoppingCartView(self.sales_service)
        self.order_view = OrderView(self.sales_service)
        self.admin_view = AdminDashboardView(self.report_service)

    # ---- 便捷方法（供演示/测试调用） ----
    def get_views(self):
        return {
            "login": self.login_view,
            "catalog": self.catalog_view,
            "cart": self.cart_view,
            "order": self.order_view,
            "admin": self.admin_view,
        }
