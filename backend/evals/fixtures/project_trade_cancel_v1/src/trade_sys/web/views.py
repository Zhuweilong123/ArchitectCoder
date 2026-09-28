"""Web 前端视图（对应 Web Frontend Views 类图）。

WebPage 为抽象基类；各视图通过 SalesApi / UserApi / ReportApi 调用后端服务。
"""
from __future__ import annotations


class WebPage:
    """前端页面基类（abstract）：所有视图页面继承统一渲染与路由跳转能力。"""

    def __init__(self, pageId: str = "", title: str = "", route: str = ""):
        self.pageId = pageId
        self.title = title
        self.route = route

    def render(self) -> None:
        raise NotImplementedError

    def navigate(self, route: str) -> None:
        self.route = route


class LoginView(WebPage):
    """登录/注册页面：校验输入后调用 UserApi 完成认证。"""

    def __init__(self, user_service):
        super().__init__("login", "登录", "/login")
        self.user_service = user_service
        self.username = ""
        self.password = ""

    def render(self) -> None:
        print(f"[LoginView] 渲染登录页 title={self.title}")

    # + onSubmit(): void
    def onSubmit(self) -> None:
        ok = self.user_service.login(self.username, self.password)
        print(f"[LoginView] 登录结果: {ok}")

    # - validateInput(): bool
    def validateInput(self) -> bool:
        return bool(self.username) and bool(self.password)


class FruitCatalogView(WebPage):
    """水果目录页：展示在售水果、价格与库存状态，支持加入购物车。"""

    def __init__(self, sales_service, inventory_service):
        super().__init__("catalog", "水果目录", "/fruits")
        self.sales_service = sales_service
        self.inventory_service = inventory_service
        self.fruitList = []

    def render(self) -> None:
        print(f"[FruitCatalogView] 在售水果: {[f.name for f in self.fruitList]}")

    # + loadFruits(): void
    def loadFruits(self) -> None:
        self.fruitList = list(self.inventory_service.db.all("fruits"))

    # + addToCart(fruitId, quantity): void
    def addToCart(self, fruitId: str, quantity: int, cart) -> None:
        fruit = self.inventory_service.queryStock(fruitId)
        if fruit is not None:
            cart.addItem(fruitId, quantity, fruit.name, fruit.price)


class ShoppingCartView(WebPage):
    """购物车页：查看选购的水果、调整数量并发起结算。"""

    def __init__(self, sales_service):
        super().__init__("cart", "购物车", "/cart")
        self.sales_service = sales_service
        self.cartItems = []

    def render(self) -> None:
        print(f"[ShoppingCartView] 购物车条目数: {len(self.cartItems)}")

    # + checkout(couponId): void
    def checkout(self, cartId: str, couponId: str = "") -> str:
        return self.sales_service.checkout(cartId, couponId)

    # + updateQuantity(itemId, quantity): void
    def updateQuantity(self, cart, itemId: str, quantity: int) -> None:
        cart.updateQuantity(itemId, quantity)


class OrderView(WebPage):
    """订单页：查看订单列表与状态、发起支付或取消订单。"""

    def __init__(self, sales_service):
        super().__init__("order", "我的订单", "/orders")
        self.sales_service = sales_service
        self.orderList = []

    def render(self) -> None:
        print(f"[OrderView] 订单数: {len(self.orderList)}")

    # + loadOrders(): void
    def loadOrders(self, customerId: str = "") -> None:
        self.orderList = self.sales_service.queryOrders()

    # + payOrder(orderId, payType): void
    def payOrder(self, orderId: str, payType: str = "WECHAT") -> None:
        ok = self.sales_service.payOrder(orderId, payType)
        print(f"[OrderView] 订单 {orderId} 支付结果: {ok}")


class AdminDashboardView(WebPage):
    """后台看板页：生成并查看经营报表（销售/库存）。"""

    def __init__(self, report_service):
        super().__init__("admin", "经营看板", "/admin")
        self.report_service = report_service
        self.reportList = []

    def render(self) -> None:
        print(f"[AdminDashboardView] 报表数: {len(self.reportList)}")

    # + loadSalesReport(date): void
    def loadSalesReport(self, date: str) -> None:
        report = self.report_service.generateDailySalesReport(date)
        self.reportList.append(report)
        print(f"[AdminDashboardView] 销售报表 {date}: 销售额={report.totalSales} 订单={report.totalOrders} 毛利={report.totalProfit}")
        print(f"[AdminDashboardView] 热销排行: {report.topFruits}")

    # + loadInventoryReport(): void
    def loadInventoryReport(self) -> None:
        report = self.report_service.generateInventoryReport()
        self.reportList.append(report)
        print(f"[AdminDashboardView] 库存报表: 库存总值={report.totalStockValue} 低库存预警={report.lowStockItems}")
        print(f"[AdminDashboardView] 临期水果={report.expiredSoonItems} 损耗={report.wasteQuantity}")
