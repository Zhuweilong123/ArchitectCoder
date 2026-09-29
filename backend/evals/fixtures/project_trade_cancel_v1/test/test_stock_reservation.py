"""库存预占闭环测试（缺口修复回归）。

覆盖超卖缺陷修复后的关键不变式：
  - 下单预占可用库存：现有库存 - 已预占，超卖被拒绝；
  - 支付提交预占：扣减现有库存并释放预占；
  - 取消待支付订单：释放预占库存；
  - 提交失败回滚：订单取消、支付退款、库存不被扣减。
所有断言基于确定性结果，不依赖线程调度。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from trade_sys.app import FruitSalesApp  # noqa: E402
from trade_sys.common import OrderStatus, PaymentStatus  # noqa: E402
from trade_sys.models.inventory import Fruit, FruitCategory  # noqa: E402
from trade_sys.models.user import Customer  # noqa: E402


def _build_app(stock=5):
    app = FruitSalesApp()
    cat = FruitCategory("CAT001", "仁果类")
    app.db.save("categories", cat.categoryId, cat)
    app.apple = app.inventory_service.register_fruit(
        Fruit("红富士苹果", cat.categoryId, "斤", price=6.5, costPrice=3.5,
              stockQuantity=stock, safetyStock=1, origin="山东"))
    return app


def _customer(app, name):
    c = Customer(name, "pwd")
    app.user_service.register(c)
    return c


def _order(app, customer, qty):
    cart = app.sales_service.create_cart(customer.userId)
    cart.addItem(app.apple.fruitId, qty, app.apple.name, app.apple.price)
    return app.cart_view.checkout(cart.cartId)


def test_available_stock_reflects_reservation():
    """下单后可用库存下降，但现有库存不变。"""
    app = _build_app(stock=5)
    cust = _customer(app, "c1")
    _order(app, cust, 3)

    assert app.apple.stockQuantity == 5
    assert app.apple.reservedQuantity == 3
    assert app.inventory_service.availableStock(app.apple.fruitId) == 2


def test_reservation_blocks_second_order_beyond_available():
    """首单预占后，超出可用库存的第二单应被拒绝（消除超卖）。"""
    app = _build_app(stock=5)
    c1 = _customer(app, "c1")
    c2 = _customer(app, "c2")

    _order(app, c1, 3)
    raised = False
    try:
        _order(app, c2, 3)
    except ValueError:
        raised = True

    assert raised, "可用库存不足时第二单应被拒绝"
    assert app.apple.reservedQuantity == 3
    assert app.inventory_service.availableStock(app.apple.fruitId) == 2


def test_sequential_orders_never_oversell():
    """库存 5 件，顺序下 10 单各 1 件：最多受理 5 单。"""
    app = _build_app(stock=5)
    accepted = 0
    for i in range(10):
        cust = _customer(app, f"c{i}")
        try:
            _order(app, cust, 1)
            accepted += 1
        except ValueError:
            pass

    assert accepted == 5, f"库存 5 件却受理了 {accepted} 单"
    assert app.inventory_service.availableStock(app.apple.fruitId) == 0
    assert app.apple.stockQuantity - app.apple.reservedQuantity >= 0


def test_pay_commits_reservation():
    """支付成功后库存被扣减，预占清零。"""
    app = _build_app(stock=20)
    cust = _customer(app, "c1")
    order_id = _order(app, cust, 2)

    assert app.sales_service.payOrder(order_id, "WECHAT") is True
    assert app.apple.stockQuantity == 18
    assert app.apple.reservedQuantity == 0
    assert app.sales_service.queryOrder(order_id).status == OrderStatus.PAID


def test_cancel_pending_order_releases_reservation():
    """取消待支付订单应释放预占库存。"""
    app = _build_app(stock=5)
    cust = _customer(app, "c1")
    order_id = _order(app, cust, 4)
    assert app.apple.reservedQuantity == 4

    assert app.sales_service.cancelOrder(order_id) is True
    assert app.apple.reservedQuantity == 0
    assert app.inventory_service.availableStock(app.apple.fruitId) == 5


def test_pay_rollback_when_reservation_missing():
    """提交预占失败时回滚：订单取消、支付退款、库存不被扣减。"""
    app = _build_app(stock=20)
    cust = _customer(app, "c1")
    order_id = _order(app, cust, 2)
    order = app.sales_service.queryOrder(order_id)

    app.apple.reservedQuantity = 0

    assert app.sales_service.payOrder(order_id, "WECHAT") is False
    assert order.status == OrderStatus.CANCELLED
    assert order.payment is not None and order.payment.status == PaymentStatus.REFUNDED
    assert app.apple.stockQuantity == 20
    assert app.apple.reservedQuantity == 0
