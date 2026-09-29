"""Fruit Sales System 演示入口。

串联设计中的三条核心顺序图：
1. Place Order Flow（下单支付）
2. Procurement Stock-in Flow（采购入库）
3. Sales Report Generation Flow（报表生成）

直接运行:  python -m trade_sys.main   （在 src 目录下）
"""
from __future__ import annotations

from .app import FruitSalesApp
from .common import today_str
from .models.user import Customer
from .models.inventory import Fruit, FruitCategory, PurchaseOrderItem
from .models.sales import Coupon
from .models.supplier import Supplier


def _banner(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def demo_place_order(app: FruitSalesApp) -> str:
    """Place Order Flow：下单 -> 校验库存 -> 计算金额 -> 支付 -> 扣减库存。"""
    _banner("1) 下单流程 (Place Order Flow)")
    # 准备顾客
    customer = Customer("alice", "pwd123", phone="13800000000")
    app.user_service.register(customer)
    customer.vipLevel = 2
    print(f"顾客注册: {customer.username} userId={customer.userId} VIP={customer.vipLevel}")

    # 顾客购物车
    cart = app.sales_service.create_cart(customer.userId)
    app.catalog_view.loadFruits()
    app.catalog_view.render()
    fruits = app.db.all("fruits")
    first, second = fruits[0], fruits[1]
    app.catalog_view.addToCart(first.fruitId, 3, cart)
    app.catalog_view.addToCart(second.fruitId, 2, cart)
    print(f"购物车合计: {cart.total()}")

    # 优惠券
    coupon = Coupon("CP001", customer.userId, "满10减5", "CASH", 5.0, minAmount=10.0)
    app.sales_service.add_coupon(coupon)

    # 结算下单
    order_id = app.cart_view.checkout(cart.cartId, "CP001")
    order = app.sales_service.queryOrder(order_id)
    print(f"创建订单: {order.orderNo} 原价={order.totalAmount} 优惠={order.discountAmount} 应付={order.payAmount}")

    # 支付
    app.order_view.payOrder(order_id, "WECHAT")
    print(f"订单状态: {order.status.name}; 支付流水: {order.payment.transactionNo}")
    return order_id


def demo_procurement(app: FruitSalesApp) -> None:
    """Procurement Stock-in Flow：创建采购单 -> 报价 -> 到货确认 -> 入库。"""
    _banner("2) 采购入库流程 (Procurement Stock-in Flow)")
    supplier = Supplier("鑫鲜果业", "张经理", phone="0571-88888888", rating=4.5)
    app.supplier_service.addSupplier(supplier)
    print(f"新增供应商: {supplier.supplierName} id={supplier.supplierId}")

    fruits = app.db.all("fruits")
    items = [PurchaseOrderItem("", fruits[0].fruitId, 100, 3.5),
             PurchaseOrderItem("", fruits[1].fruitId, 50, 4.0)]
    purchase_no = app.inventory_service.createPurchaseOrder(supplier.supplierId, items, operatorId="ADMIN001")
    print(f"创建采购单: {purchase_no} 金额={round(sum(i.subtotal for i in items), 2)}")

    # 到货确认并入库
    po = next(p for p in app.db.all("purchase_orders") if p.purchaseNo == purchase_no)
    stock_before = app.inventory_service.queryStock(items[0].fruitId).stockQuantity
    app.inventory_service.confirmArrival(po.purchaseId)
    stock_after = app.inventory_service.queryStock(items[0].fruitId).stockQuantity
    print(f"到货确认: {fruits[0].name} 库存 {stock_before} -> {stock_after}; 采购单状态={po.status.name}")


def demo_report(app: FruitSalesApp) -> None:
    """Sales Report Generation Flow：生成销售报表并导出。"""
    _banner("3) 报表生成流程 (Sales Report Generation Flow)")
    date = today_str()
    app.admin_view.loadSalesReport(date)
    report = app.admin_view.reportList[-1]
    filename = app.report_service.exportExcel(report.reportId)
    print(f"报表已导出: {filename}")

    inv_report = app.report_service.generateInventoryReport()
    print(f"库存报表: 库存总值={inv_report.totalStockValue} 低库存={inv_report.lowStockItems}")


def main() -> None:
    app = FruitSalesApp()
    # 初始化基础数据：分类与水果
    category = FruitCategory("CAT001", "仁果类", "苹果、梨等")
    app.db.save("categories", category.categoryId, category)
    apple = app.inventory_service.register_fruit(
        Fruit("红富士苹果", category.categoryId, "斤", price=6.5, costPrice=3.5,
              stockQuantity=20, safetyStock=10, shelfLife=15, origin="山东"))
    banana = app.inventory_service.register_fruit(
        Fruit("进口香蕉", category.categoryId, "斤", price=4.2, costPrice=2.0,
              stockQuantity=15, safetyStock=5, shelfLife=7, origin="菲律宾"))
    print(f"初始化水果: {apple.name}(id={apple.fruitId}), {banana.name}(id={banana.fruitId})")

    demo_place_order(app)
    demo_procurement(app)
    demo_report(app)

    _banner("演示完成")


if __name__ == "__main__":
    main()
