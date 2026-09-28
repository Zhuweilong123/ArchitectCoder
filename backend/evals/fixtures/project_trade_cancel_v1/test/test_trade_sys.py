"""端到端测试：验证三条核心顺序图流程与关键不变量。"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from trade_sys.app import FruitSalesApp  # noqa: E402
from trade_sys.common import OrderStatus, PurchaseStatus  # noqa: E402
from trade_sys.models.inventory import Fruit, FruitCategory, PurchaseOrderItem  # noqa: E402
from trade_sys.models.sales import Coupon  # noqa: E402
from trade_sys.models.supplier import Supplier  # noqa: E402
from trade_sys.models.user import Customer  # noqa: E402


def _build_app():
    app = FruitSalesApp()
    cat = FruitCategory("CAT001", "仁果类")
    app.db.save("categories", cat.categoryId, cat)
    app.apple = app.inventory_service.register_fruit(
        Fruit("红富士苹果", cat.categoryId, "斤", price=6.5, costPrice=3.5,
              stockQuantity=20, safetyStock=10, origin="山东"))
    app.banana = app.inventory_service.register_fruit(
        Fruit("进口香蕉", cat.categoryId, "斤", price=4.2, costPrice=2.0,
              stockQuantity=15, safetyStock=5, origin="菲律宾"))
    return app


def test_place_order_flow():
    app = _build_app()
    customer = Customer("alice", "pwd", phone="138")
    app.user_service.register(customer)
    customer.vipLevel = 2  # 折扣系数 0.95
    cart = app.sales_service.create_cart(customer.userId)
    cart.addItem(app.apple.fruitId, 3, app.apple.name, app.apple.price)
    coupon = Coupon("CP001", customer.userId, "满10减5", "CASH", 5.0, minAmount=10.0)
    app.sales_service.add_coupon(coupon)

    stock_before = app.apple.stockQuantity
    order_id = app.cart_view.checkout(cart.cartId, "CP001")
    order = app.sales_service.queryOrder(order_id)
    # 原价 6.5*3=19.5，优惠 5，(19.5-5)*0.05=0.725 -> 总优惠 5.73，应付 13.77
    assert order.totalAmount == 19.5
    assert order.discountAmount == 5.73
    assert order.payAmount == 13.77
    assert app.sales_service.payOrder(order_id, "WECHAT") is True
    assert order.status == OrderStatus.PAID
    # 支付成功后库存扣减
    assert app.apple.stockQuantity == stock_before - 3


def test_procurement_stockin_flow():
    app = _build_app()
    supplier = Supplier("鑫鲜果业", rating=4.5)
    app.supplier_service.addSupplier(supplier)
    items = [PurchaseOrderItem("", app.apple.fruitId, 100, 3.5)]
    purchase_no = app.inventory_service.createPurchaseOrder(supplier.supplierId, items, operatorId="ADMIN")
    po = next(p for p in app.db.all("purchase_orders") if p.purchaseNo == purchase_no)
    stock_before = app.apple.stockQuantity
    assert app.inventory_service.confirmArrival(po.purchaseId) is True
    assert po.status == PurchaseStatus.ARRIVED
    assert app.apple.stockQuantity == stock_before + 100


def test_report_flow():
    app = _build_app()
    customer = Customer("bob", "pwd")
    app.user_service.register(customer)
    cart = app.sales_service.create_cart(customer.userId)
    cart.addItem(app.banana.fruitId, 2, app.banana.name, app.banana.price)
    order_id = app.cart_view.checkout(cart.cartId)
    order = app.sales_service.queryOrder(order_id)
    app.sales_service.payOrder(order_id, "ALIPAY")

    report = app.report_service.generateDailySalesReport(order.createTime[:10])
    assert report.totalOrders == 1
    assert report.totalSales == order.payAmount
    assert app.report_service.exportExcel(report.reportId).endswith(".xlsx")


def test_insufficient_stock_rejected():
    app = _build_app()
    customer = Customer("carol", "pwd")
    app.user_service.register(customer)
    cart = app.sales_service.create_cart(customer.userId)
    cart.addItem(app.apple.fruitId, 9999, app.apple.name, app.apple.price)
    try:
        app.cart_view.checkout(cart.cartId)
        assert False, "库存不足时应抛出异常"
    except ValueError:
        pass
