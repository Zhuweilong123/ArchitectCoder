"""关键流程补充用例。

覆盖三条核心顺序图（Place Order / Procurement Stock-in / Sales Report）的
主路径与关键分支，以及支撑性能力（库存服务、用户权限、供应商、购物车）。
所有断言基于当前设计生成的实现语义，不修改业务代码。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from trade_sys.app import FruitSalesApp  # noqa: E402
from trade_sys.common import (  # noqa: E402
    CouponStatus, OrderStatus, PurchaseStatus, StockRecordType, UserStatus,
)
from trade_sys.models.inventory import Fruit, FruitCategory, PurchaseOrderItem  # noqa: E402
from trade_sys.models.sales import Coupon  # noqa: E402
from trade_sys.models.supplier import Supplier  # noqa: E402
from trade_sys.models.user import Admin, Customer, Permission, Role  # noqa: E402


# --------------------------------------------------------------------------- #
# 测试夹具
# --------------------------------------------------------------------------- #
def _build_app():
    """构建一个带分类与两种水果的最小可用系统。"""
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


def _new_customer(app, name="cust", vip=0):
    c = Customer(name, "pwd")
    app.user_service.register(c)
    c.vipLevel = vip
    return c


def _cart_with(app, customer, fruit, qty):
    cart = app.sales_service.create_cart(customer.userId)
    cart.addItem(fruit.fruitId, qty, fruit.name, fruit.price)
    return cart


# --------------------------------------------------------------------------- #
# Place Order Flow
# --------------------------------------------------------------------------- #
def test_place_order_no_discount():
    """无优惠券且非 VIP：应付等于原价，支付后扣减库存并生成支付记录。"""
    app = _build_app()
    customer = _new_customer(app)
    cart = _cart_with(app, customer, app.apple, 2)

    order_id = app.cart_view.checkout(cart.cartId)
    order = app.sales_service.queryOrder(order_id)

    assert order.totalAmount == 13.0
    assert order.discountAmount == 0.0
    assert order.payAmount == 13.0
    assert order.status == OrderStatus.PENDING_PAYMENT
    assert cart.items == []  # 结算后购物车清空

    assert app.sales_service.payOrder(order_id, "ALIPAY") is True
    assert order.status == OrderStatus.PAID
    assert order.payment is not None and order.payment.payType == "ALIPAY"


def test_coupon_below_min_amount_not_applied():
    """优惠券未达最低消费门槛时不应抵扣。"""
    app = _build_app()
    customer = _new_customer(app)
    cart = _cart_with(app, customer, app.apple, 1)  # 6.5 元
    coupon = Coupon("CP_LOW", customer.userId, "满100减50", "CASH", 50.0, minAmount=100.0)
    app.sales_service.add_coupon(coupon)

    order_id = app.cart_view.checkout(cart.cartId, "CP_LOW")
    order = app.sales_service.queryOrder(order_id)

    assert order.discountAmount == 0.0
    assert order.payAmount == 6.5


def test_coupon_meets_min_amount_applied():
    """满减券在满足门槛时按面额抵扣。"""
    app = _build_app()
    customer = _new_customer(app)
    cart = _cart_with(app, customer, app.apple, 2)  # 13 元
    coupon = Coupon("CP_OK", customer.userId, "满10减5", "CASH", 5.0, minAmount=10.0)
    app.sales_service.add_coupon(coupon)

    order_id = app.cart_view.checkout(cart.cartId, "CP_OK")
    order = app.sales_service.queryOrder(order_id)

    assert order.totalAmount == 13.0
    assert order.discountAmount == 5.0
    assert order.payAmount == 8.0


def test_discount_coupon_applied():
    """折扣券：discountValue 为折扣系数（0.9 表示 9 折）。"""
    app = _build_app()
    customer = _new_customer(app)
    cart = _cart_with(app, customer, app.apple, 2)  # 13 元
    coupon = Coupon("CP_DISC", customer.userId, "9折券", "DISCOUNT", 0.9, minAmount=10.0)
    app.sales_service.add_coupon(coupon)

    order_id = app.cart_view.checkout(cart.cartId, "CP_DISC")
    order = app.sales_service.queryOrder(order_id)

    assert order.discountAmount == 1.3
    assert order.payAmount == 11.7


def test_vip_discount_stacked_with_coupon():
    """VIP 折扣在扣除优惠券后叠加（原先例数值的独立验证）。"""
    app = _build_app()
    customer = _new_customer(app, vip=2)  # 系数 0.95
    cart = _cart_with(app, customer, app.apple, 3)  # 19.5 元
    coupon = Coupon("CP_VIP", customer.userId, "满10减5", "CASH", 5.0, minAmount=10.0)
    app.sales_service.add_coupon(coupon)

    order_id = app.cart_view.checkout(cart.cartId, "CP_VIP")
    order = app.sales_service.queryOrder(order_id)

    assert order.totalAmount == 19.5
    assert order.discountAmount == 5.73
    assert order.payAmount == 13.77


def test_vip_no_coupon():
    """VIP4 无券：仅按等级折扣。"""
    app = _build_app()
    customer = _new_customer(app, vip=4)  # 系数 0.88
    cart = _cart_with(app, customer, app.apple, 3)  # 19.5 元

    order_id = app.cart_view.checkout(cart.cartId)
    order = app.sales_service.queryOrder(order_id)

    assert order.discountAmount == 2.34
    assert order.payAmount == 17.16


def test_pay_order_twice_rejected():
    """重复支付应被拒绝（状态机约束）。"""
    app = _build_app()
    customer = _new_customer(app)
    cart = _cart_with(app, customer, app.apple, 1)
    order_id = app.cart_view.checkout(cart.cartId)

    assert app.sales_service.payOrder(order_id, "WECHAT") is True
    assert app.sales_service.payOrder(order_id, "WECHAT") is False


def test_cancel_pending_order():
    """待支付订单可取消，取消后状态为 CANCELLED。"""
    app = _build_app()
    customer = _new_customer(app)
    cart = _cart_with(app, customer, app.apple, 1)
    order_id = app.cart_view.checkout(cart.cartId)

    assert app.sales_service.cancelOrder(order_id) is True
    assert app.sales_service.queryOrder(order_id).status == OrderStatus.CANCELLED


def test_checkout_unknown_cart_raises():
    """结算不存在的购物车应抛出异常。"""
    app = _build_app()
    try:
        app.cart_view.checkout("C_NOT_EXIST")
        assert False, "不存在的购物车应抛出 ValueError"
    except ValueError:
        pass


# --------------------------------------------------------------------------- #
# 库存服务分支
# --------------------------------------------------------------------------- #
def test_stock_out_insufficient_returns_false():
    """出库量超过库存时应失败且不改动库存。"""
    app = _build_app()
    before = app.apple.stockQuantity
    assert app.inventory_service.stockOut(app.apple.fruitId, 9999, "NO_X") is False
    assert app.apple.stockQuantity == before


def test_check_stock_unknown_fruit_false():
    """对不存在的水果查询库存返回 False。"""
    app = _build_app()
    assert app.inventory_service.checkStock("F_NOT_EXIST", 1) is False


def test_stock_in_rejects_non_positive_quantity():
    """入库数量非正数时应失败。"""
    app = _build_app()
    before = app.apple.stockQuantity
    assert app.inventory_service.stockIn(app.apple.fruitId, 0, "ADMIN") is False
    assert app.apple.stockQuantity == before


def test_stock_increments_and_records():
    """入库成功应增加库存并登记 IN 记录。"""
    app = _build_app()
    before = app.apple.stockQuantity
    assert app.inventory_service.stockIn(app.apple.fruitId, 5, "ADMIN") is True
    assert app.apple.stockQuantity == before + 5
    recs = [r for r in app.db.all("inventory_records") if r.fruitId == app.apple.fruitId]
    assert any(r.type == StockRecordType.IN and r.quantity == 5 for r in recs)


# --------------------------------------------------------------------------- #
# Procurement Stock-in Flow
# --------------------------------------------------------------------------- #
def test_procurement_quote_fills_unit_price():
    """采购明细单价为 0 时应由供应商报价回填，并计算小计与总额。"""
    app = _build_app()
    supplier = Supplier("鑫鲜果业", rating=4.5)
    app.supplier_service.addSupplier(supplier)

    items = [PurchaseOrderItem("", app.apple.fruitId, 100, 0.0)]
    purchase_no = app.inventory_service.createPurchaseOrder(
        supplier.supplierId, items, operatorId="ADMIN")
    po = next(p for p in app.db.all("purchase_orders") if p.purchaseNo == purchase_no)

    assert po.items[0].unitPrice == 3.5  # SupplierApi.queryQuote 固定报价
    assert po.items[0].subtotal == 350.0
    assert po.totalAmount == 350.0
    assert po.status == PurchaseStatus.PENDING_ARRIVAL


def test_confirm_arrival_stockin_then_twice_rejected():
    """到货确认应入库并可追溯；重复确认应被拒绝。"""
    app = _build_app()
    supplier = Supplier("鑫鲜果业", rating=4.5)
    app.supplier_service.addSupplier(supplier)
    purchase_no = app.inventory_service.createPurchaseOrder(
        supplier.supplierId, [PurchaseOrderItem("", app.apple.fruitId, 100, 3.5)], "ADMIN")
    po = next(p for p in app.db.all("purchase_orders") if p.purchaseNo == purchase_no)
    before = app.apple.stockQuantity

    assert app.inventory_service.confirmArrival(po.purchaseId) is True
    assert po.status == PurchaseStatus.ARRIVED
    assert app.apple.stockQuantity == before + 100
    assert app.inventory_service.confirmArrival(po.purchaseId) is False


def test_cancel_purchase_then_arrival_rejected():
    """采购单取消后不能再确认到货。"""
    app = _build_app()
    supplier = Supplier("临时供应商")
    app.supplier_service.addSupplier(supplier)
    purchase_no = app.inventory_service.createPurchaseOrder(
        supplier.supplierId, [PurchaseOrderItem("", app.apple.fruitId, 10, 3.5)], "ADMIN")
    po = next(p for p in app.db.all("purchase_orders") if p.purchaseNo == purchase_no)

    assert po.cancelPurchase() is True
    assert po.status == PurchaseStatus.CANCELLED
    assert app.inventory_service.confirmArrival(po.purchaseId) is False


def test_supplier_management():
    """供应商新增/更新/评价分支。"""
    app = _build_app()
    supplier = Supplier("低分供应商", rating=2.0)
    app.supplier_service.addSupplier(supplier)

    assert app.supplier_service.updateSupplier(supplier) is True
    missing = Supplier("不存在")
    assert app.supplier_service.updateSupplier(missing) is False

    app.supplier_service.evaluateSupplier(supplier.supplierId)
    assert supplier.status == 0  # 低于 3.0 进入汰汰名单


# --------------------------------------------------------------------------- #
# Sales Report Generation Flow
# --------------------------------------------------------------------------- #
def test_daily_report_sales_profit_and_ranking():
    """日报：销售额、订单数、毛利（销售额-出库成本）与热销排行。"""
    app = _build_app()
    customer = _new_customer(app)
    cart = _cart_with(app, customer, app.banana, 2)  # 售 8.4，成本 2*2.0=4.0
    order_id = app.cart_view.checkout(cart.cartId)
    order = app.sales_service.queryOrder(order_id)
    app.sales_service.payOrder(order_id, "ALIPAY")

    report = app.report_service.generateDailySalesReport(order.createTime[:10])
    assert report.totalOrders == 1
    assert report.totalSales == 8.4
    assert report.totalProfit == 4.4
    assert report.topFruits == ["进口香蕉"]


def test_daily_report_empty_day():
    """无订单日期报表应为零值。"""
    app = _build_app()
    report = app.report_service.generateDailySalesReport("1970-01-01")
    assert report.totalOrders == 0
    assert report.totalSales == 0.0
    assert report.totalProfit == 0.0


def test_monthly_report_aggregates_orders():
    """月报聚合当日订单，数值与日报一致。"""
    app = _build_app()
    customer = _new_customer(app)
    cart = _cart_with(app, customer, app.banana, 2)
    order_id = app.cart_view.checkout(cart.cartId)
    order = app.sales_service.queryOrder(order_id)
    app.sales_service.payOrder(order_id, "WECHAT")

    report = app.report_service.generateMonthlyReport(order.createTime[:7])
    assert report.totalOrders == 1
    assert report.totalSales == 8.4


def test_inventory_report_value_and_low_stock_alert():
    """库存报表：库存总值与低库存预警。"""
    app = _build_app()
    cat = app.db.get("categories", "CAT001")
    low = app.inventory_service.register_fruit(
        Fruit("临期蓝莓", cat.categoryId, "盒", price=9.9, costPrice=1.0,
              stockQuantity=2, safetyStock=10))

    report = app.report_service.generateInventoryReport()
    # 苹果 20*3.5=70 + 香蕉 15*2.0=30 + 蓝莓 2*1.0=2 = 102.0
    assert report.totalStockValue == 102.0
    assert low.name in report.lowStockItems


def test_export_excel_unknown_report_empty():
    """导出不存在的报表返回空字符串。"""
    app = _build_app()
    assert app.report_service.exportExcel("SR_NOT_EXIST") == ""


# --------------------------------------------------------------------------- #
# 支撑能力：用户/权限、购物车
# --------------------------------------------------------------------------- #
def test_login_and_change_password():
    """登录成功/失败与修改密码。"""
    app = _build_app()
    customer = _new_customer(app, name="dave")

    assert app.user_service.login("dave", "pwd") is True
    assert app.user_service.login("dave", "wrong") is False
    assert app.user_service.login("nobody", "pwd") is False

    assert customer.changePassword("pwd", "newpwd") is True
    assert app.user_service.login("dave", "newpwd") is True
    assert customer.changePassword("bad", "x") is False


def test_disabled_user_cannot_change_password():
    """停用用户无法修改密码（validate 约束）。"""
    app = _build_app()
    customer = _new_customer(app, name="erin")
    customer.status = UserStatus.DISABLED
    assert customer.changePassword("pwd", "newpwd") is False


def test_role_permission_grant_and_check():
    """角色授予权限后 checkPermission 通过。"""
    app = _build_app()
    admin = Admin("manager", "pwd", department="门店")
    app.user_service.register(admin)
    role = Role("R1", "店长")
    perm = Permission("PM1", "manage_fruit", "管理水果")
    app.db.save("roles", role.roleId, role)
    app.db.save("permissions", perm.permId, perm)
    app.db.role_permissions[role.roleId] = [perm.permId]

    assert app.user_service.checkPermission(admin.userId, "manage_fruit") is False  # 尚未分配角色
    app.user_service.assignRole(admin.userId, role.roleId)
    assert app.user_service.checkPermission(admin.userId, "manage_fruit") is True
    assert app.user_service.checkPermission(admin.userId, "manage_order") is False


def test_cart_merge_remove_and_update_quantity():
    """购物车：同商品累加、移除、改数量与合计。"""
    app = _build_app()
    customer = _new_customer(app)
    cart = app.sales_service.create_cart(customer.userId)

    cart.addItem(app.apple.fruitId, 1, app.apple.name, app.apple.price)
    cart.addItem(app.apple.fruitId, 2, app.apple.name, app.apple.price)  # 合并为 3
    assert len(cart.items) == 1
    assert cart.items[0].quantity == 3
    assert cart.total() == 19.5

    item_id = cart.items[0].cartItemId
    cart.updateQuantity(item_id, 1)
    assert cart.total() == 6.5

    cart.removeItem(item_id)
    assert cart.items == []
    assert cart.total() == 0.0


def test_coupon_validate_and_use():
    """优惠券：满足门槛可校验通过，使用后状态置为已使用。"""
    app = _build_app()
    coupon = Coupon("CP1", "U1", "满10减2", "CASH", 2.0, minAmount=10.0)

    assert coupon.validate(9.99) is False
    assert coupon.validate(10.0) is True
    coupon.use()
    assert coupon.status == CouponStatus.USED
    assert coupon.validate(20.0) is False


# --------------------------------------------------------------------------- #
# Web Frontend Views
# --------------------------------------------------------------------------- #
def test_web_views_render_and_actions():
    """前端视图：登录、目录加载、下单、支付与后台报表加载均可运行。"""
    app = _build_app()
    customer = _new_customer(app, name="frank")

    # 登录页
    app.login_view.username = "frank"
    app.login_view.password = "pwd"
    assert app.login_view.validateInput() is True
    app.login_view.onSubmit()

    # 目录页：加载在售水果并加入购物车
    app.catalog_view.loadFruits()
    assert len(app.catalog_view.fruitList) == 2
    cart = app.sales_service.create_cart(customer.userId)
    app.catalog_view.addToCart(app.apple.fruitId, 1, cart)
    assert len(cart.items) == 1

    # 购物车页：结算生成订单
    order_id = app.cart_view.checkout(cart.cartId)
    assert app.sales_service.queryOrder(order_id) is not None

    # 订单页：加载订单并支付
    app.order_view.loadOrders(customer.userId)
    assert len(app.order_view.orderList) >= 1
    app.order_view.payOrder(order_id, "WECHAT")
    assert app.sales_service.queryOrder(order_id).status == OrderStatus.PAID


def test_admin_dashboard_loads_sales_and_inventory_reports():
    """后台看板：loadSalesReport 与 loadInventoryReport 均生成并记录报表。"""
    app = _build_app()
    customer = _new_customer(app, name="gina")
    cart = _cart_with(app, customer, app.banana, 2)
    order_id = app.cart_view.checkout(cart.cartId)
    order = app.sales_service.queryOrder(order_id)
    app.sales_service.payOrder(order_id, "WECHAT")

    app.admin_view.loadSalesReport(order.createTime[:10])
    assert len(app.admin_view.reportList) == 1

    # 设计声明的方法：AdminDashboardView.loadInventoryReport()
    app.admin_view.loadInventoryReport()
    assert len(app.admin_view.reportList) == 2
    inv_report = app.admin_view.reportList[-1]
    # 支付 2 斤香蕉后：苹果 20*3.5=70 + 香蕉 (15-2)*2.0=26 = 96.0
    assert inv_report.totalStockValue == 96.0
