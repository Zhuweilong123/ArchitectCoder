"""超卖缺陷复现用例（库存不足时的并发下单）。

场景：同一商品库存仅 STOCK 件，但有 CUSTOMERS 个顾客各自购买 1 件（CUSTOMERS > STOCK）。
期望：系统最多只能受理 STOCK 件，库存不得为负。

现状（缺陷）：
  - SalesService.createOrder 只做即时校验、**不做库存预留**，因此多于库存的订单
    仍可全部创建成功；
  - 库存扣减发生在 SalesService.payOrder，其内部 InventoryService.stockOut 是
    “先查后减”（check-then-act）且返回值被忽略；库存扣光后剩余订单仍被置为 PAID，
    仅静默不扣减。

因此本用例在当前实现下应**稳定失败（红）**：受理/支付成功的件数会超过可用库存。
修复（下单预占 / 支付前二次校验 + 扣减失败回滚）后，本用例应转为通过，
可直接作为该缺陷的回归测试。

断言刻意只依赖确定性结果（成功受理件数、库存数值），不依赖线程调度是否恰好重叠，
以保证重复执行结果一致；线程屏障仅用于让并发路径尽量重叠。
"""
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from trade_sys.app import FruitSalesApp  # noqa: E402
from trade_sys.common import OrderStatus  # noqa: E402
from trade_sys.models.inventory import Fruit, FruitCategory  # noqa: E402
from trade_sys.models.user import Customer  # noqa: E402


STOCK = 5          # 可用库存
CUSTOMERS = 20     # 并发顾客数（每人买 1 件，远大于库存）


def _build_app():
    """构建一个库存受限的最小可用系统：只有一种水果，库存 STOCK 件。"""
    app = FruitSalesApp()
    cat = FruitCategory("CAT001", "仁果类")
    app.db.save("categories", cat.categoryId, cat)
    app.apple = app.inventory_service.register_fruit(
        Fruit("红富士苹果", cat.categoryId, "斤", price=6.5, costPrice=3.5,
              stockQuantity=STOCK, safetyStock=1, origin="山东"))
    return app


def test_concurrent_orders_must_not_oversell():
    app = _build_app()
    fruit = app.apple
    assert fruit.stockQuantity == STOCK, "前置条件：初始库存应等于 STOCK"

    # 1) 每位顾客各自下单（每单 1 件）。下单阶段不扣减库存，故可用库存不足时
    #    仍应被拒绝——这里用 try/except 接纳被正确拒绝的订单，使本用例在修复后依然成立。
    order_ids = []
    for i in range(CUSTOMERS):
        customer = Customer(f"cust{i}", "pwd")
        app.user_service.register(customer)
        cart = app.sales_service.create_cart(customer.userId)
        cart.addItem(fruit.fruitId, 1, fruit.name, fruit.price)
        try:
            order_ids.append(app.cart_view.checkout(cart.cartId))
        except ValueError:
            # 修复后：超出库存的下单被拒绝，属预期行为
            pass

    accepted_units = len(order_ids)

    # 2) 并发支付。库存扣减发生在支付成功后，是 stockOut 内 check-then-act 的竞态点。
    #    使用屏障让所有线程尽量同时进入扣减路径。
    barrier = threading.Barrier(len(order_ids)) if order_ids else None

    def _pay(order_id):
        if barrier is not None:
            barrier.wait()
        return app.sales_service.payOrder(order_id, "WECHAT")

    with ThreadPoolExecutor(max_workers=max(1, len(order_ids))) as pool:
        list(pool.map(_pay, order_ids))

    # 3) 统计实际“支付成功”的订单件数（= 系统对外承诺已成交的数量）
    paid_units = sum(
        item.quantity
        for order in app.db.all("orders")
        if order.status == OrderStatus.PAID
        for item in order.items
    )
    remaining = fruit.stockQuantity

    # 4) 核心不变量：受理/成交件数不得超过可用库存，且库存不得为负。
    assert accepted_units <= STOCK, (
        f"超卖：库存仅 {STOCK} 件，却受理了 {accepted_units} 件下单"
    )
    assert paid_units <= STOCK, (
        f"超卖：库存仅 {STOCK} 件，却有 {paid_units} 件订单支付成功"
    )
    assert remaining >= 0, f"库存被扣成负数：{remaining}"
    assert remaining == STOCK - paid_units, (
        f"库存账实不符：剩余 {remaining}，按成交应为 {STOCK - paid_units}"
    )
