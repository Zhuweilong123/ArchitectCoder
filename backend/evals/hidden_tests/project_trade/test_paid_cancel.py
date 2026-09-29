"""Deterministic acceptance oracle for the trade orchestration case."""

from trade_sys.app import FruitSalesApp
from trade_sys.common import OrderStatus, PaymentStatus
from trade_sys.models.inventory import Fruit, FruitCategory
from trade_sys.models.user import Customer


def _app():
    app = FruitSalesApp()
    category = FruitCategory("CAT-EVAL", "评测水果")
    app.db.save("categories", category.categoryId, category)
    apple = app.inventory_service.register_fruit(
        Fruit("苹果", category.categoryId, "斤", price=10.0,
              costPrice=4.0, stockQuantity=20, safetyStock=1),
    )
    banana = app.inventory_service.register_fruit(
        Fruit("香蕉", category.categoryId, "斤", price=6.0,
              costPrice=2.0, stockQuantity=20, safetyStock=1),
    )
    customer = Customer("eval_customer", "pwd")
    app.user_service.register(customer)
    return app, apple, banana, customer


def _order(app, customer, items):
    cart = app.sales_service.create_cart(customer.userId)
    for fruit, quantity in items:
        cart.addItem(fruit.fruitId, quantity, fruit.name, fruit.price)
    return app.cart_view.checkout(cart.cartId)


def test_paid_cancel_refunds_and_restores_each_item_once():
    app, apple, banana, customer = _app()
    order_id = _order(app, customer, [(apple, 2), (banana, 3)])
    order = app.sales_service.queryOrder(order_id)
    assert app.sales_service.payOrder(order_id, "WECHAT") is True
    assert (apple.stockQuantity, banana.stockQuantity) == (18, 17)

    assert app.sales_service.cancelOrder(order_id) is True
    assert order.status == OrderStatus.CANCELLED
    assert order.payment is not None
    assert order.payment.status == PaymentStatus.REFUNDED
    assert (apple.stockQuantity, banana.stockQuantity) == (20, 20)
    assert (apple.reservedQuantity, banana.reservedQuantity) == (0, 0)

    before_records = len(app.db.all("inventory_records"))
    payment = order.payment
    refund_calls = []
    original_refund = order.payment.refund

    def track_refund(payment_id=""):
        refund_calls.append(payment_id)
        return original_refund(payment_id)

    order.payment.refund = track_refund
    app.sales_service.cancelOrder(order_id)
    assert (apple.stockQuantity, banana.stockQuantity) == (20, 20)
    assert order.payment is payment
    assert order.payment.status == PaymentStatus.REFUNDED
    assert len(app.db.all("inventory_records")) == before_records
    assert refund_calls == []


def test_pending_cancel_releases_reservation_without_refund_or_restock():
    app, apple, _, customer = _app()
    order_id = _order(app, customer, [(apple, 4)])
    order = app.sales_service.queryOrder(order_id)
    assert apple.reservedQuantity == 4

    assert app.sales_service.cancelOrder(order_id) is True
    assert order.status == OrderStatus.CANCELLED
    assert order.payment is None
    assert apple.stockQuantity == 20
    assert apple.reservedQuantity == 0
    before_records = len(app.db.all("inventory_records"))
    app.sales_service.cancelOrder(order_id)
    assert apple.stockQuantity == 20
    assert apple.reservedQuantity == 0
    assert len(app.db.all("inventory_records")) == before_records


def test_shipped_order_cannot_refund_or_restock():
    app, apple, _, customer = _app()
    order_id = _order(app, customer, [(apple, 1)])
    assert app.sales_service.payOrder(order_id, "ALIPAY") is True
    order = app.sales_service.queryOrder(order_id)
    order.updateStatus(OrderStatus.SHIPPED)

    assert app.sales_service.cancelOrder(order_id) is False
    assert order.status == OrderStatus.SHIPPED
    assert order.payment.status == PaymentStatus.SUCCESS
    assert apple.stockQuantity == 19


def test_daily_and_monthly_reports_exclude_cancelled_sale_and_cost():
    app, apple, banana, customer = _app()
    cancelled_id = _order(app, customer, [(apple, 2)])
    pending_id = _order(app, customer, [(apple, 1)])
    kept_id = _order(app, customer, [(banana, 3)])
    cancelled = app.sales_service.queryOrder(cancelled_id)

    assert app.sales_service.payOrder(cancelled_id, "WECHAT") is True
    assert app.sales_service.payOrder(kept_id, "WECHAT") is True
    assert app.sales_service.cancelOrder(cancelled_id) is True
    assert app.sales_service.queryOrder(pending_id).status == OrderStatus.PENDING_PAYMENT

    for report in (
        app.report_service.generateDailySalesReport(cancelled.createTime[:10]),
        app.report_service.generateMonthlyReport(cancelled.createTime[:7]),
    ):
        assert report.totalOrders == 1
        assert report.totalSales == 18.0
        assert report.totalProfit == 12.0
        assert report.topFruits == ["香蕉"]
