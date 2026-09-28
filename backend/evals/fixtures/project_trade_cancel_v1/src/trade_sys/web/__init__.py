"""Web 前端视图层（对应 Web Frontend Views 类图 / WebFrontend 组件）。"""

from .views import (
    WebPage, LoginView, FruitCatalogView, ShoppingCartView, OrderView, AdminDashboardView,
)

__all__ = [
    "WebPage", "LoginView", "FruitCatalogView", "ShoppingCartView", "OrderView", "AdminDashboardView",
]
