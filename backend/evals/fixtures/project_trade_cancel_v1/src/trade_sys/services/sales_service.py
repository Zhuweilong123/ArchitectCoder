"""销售服务（SalesService，提供 SalesApi）。

对应 Sales Domain 类图，并实现“Place Order Flow”顺序图：
createOrder -> checkStock -> calculateAmount -> pay -> stockOut。
需要请求 InventoryApi、UserApi 与 DatabaseClient。
"""
from __future__ import annotations

from typing import List, Optional

from ..common import OrderStatus
from ..database import Database
from ..models.sales import Coupon, Order, OrderItem, Payment, ShoppingCart


class SalesService:
    def __init__(self, db: Database, inventory_service=None, user_service=None):
        self.db = db
        self.inventory_service = inventory_service
        self.user_service = user_service

    # + createOrder(customerId, cartItems): string
    def createOrder(self, customerId: str, cartItems: List, couponId: str = "") -> str:
        order = Order(customerId)
        # msg_03：逐项预占可用库存，任一不足则整单失败并释放本单已预占库存
        reserved = []
        for ci in cartItems:
            if self.inventory_service:
                if not self.inventory_service.reserveStock(ci.fruitId, ci.quantity, order.orderNo):
                    for done in reserved:
                        self.inventory_service.releaseStock(done.fruitId, done.quantity, order.orderNo)
                    raise ValueError(f"库存不足: {ci.fruitName or ci.fruitId}")
                reserved.append(ci)
            order.add_item(OrderItem(order.orderId, ci.fruitId, ci.fruitName, ci.unitPrice, ci.quantity))
        # msg_05：计算订单金额（含优惠券折扣与 VIP 折扣）
        coupon = self.db.get("coupons", couponId) if couponId else None
        vip_rate = 1.0
        if self.user_service is not None:
            customer = self.db.get("users", customerId)
            if customer is not None and hasattr(customer, "discount_rate"):
                vip_rate = customer.discount_rate()
        order.calculateAmount(coupon, vip_rate)
        if coupon is not None:
            order.coupon = coupon
        self.db.save("orders", order.orderId, order)
        return order.orderId

    # + cancelOrder(orderId): bool
    def cancelOrder(self, orderId: str) -> bool:
        order = self.queryOrder(orderId)
        if order is None:
            return False
        # 待支付订单取消时释放其已预占库存
        if order.status == OrderStatus.PENDING_PAYMENT and self.inventory_service:
            for item in order.items:
                self.inventory_service.releaseStock(item.fruitId, item.quantity, order.orderNo)
        return order.cancel()

    # + checkout(cartId, couponId): string
    def checkout(self, cartId: str, couponId: str = "") -> str:
        cart = self.db.get("carts", cartId)
        if cart is None:
            raise ValueError("购物车不存在")
        order_id = self.createOrder(cart.customerId, cart.items, couponId)
        cart.clear()
        return order_id

    # + payOrder(orderId, payType): bool
    def payOrder(self, orderId: str, payType: str = "WECHAT") -> bool:
        order = self.queryOrder(orderId)
        if order is None or order.status != OrderStatus.PENDING_PAYMENT:
            return False
        # msg_06：发起支付并生成支付记录
        payment = Payment(orderId, payType, order.payAmount)
        payment.pay(orderId, payType, order.payAmount)
        order.payment = payment
        self.db.save("payments", payment.paymentId, payment)
        # msg_08：支付成功后提交预占（扣减现有库存并释放预占），任一项失败则回滚
        if self.inventory_service:
            committed = []
            for item in order.items:
                if not self.inventory_service.commitReservation(item.fruitId, item.quantity, order.orderNo):
                    # 回滚已提交库存，释放其余预占，退款并取消订单
                    for done in committed:
                        self.inventory_service.stockIn(done.fruitId, done.quantity, order.orderNo)
                    for rest in order.items:
                        if rest not in committed:
                            self.inventory_service.releaseStock(rest.fruitId, rest.quantity, order.orderNo)
                    payment.refund(payment.paymentId)
                    order.updateStatus(OrderStatus.CANCELLED)
                    return False
                committed.append(item)
        order.updateStatus(OrderStatus.PAID)
        return True

    # + queryOrder(orderId): Order
    def queryOrder(self, orderId: str) -> Optional[Order]:
        return self.db.get("orders", orderId)

    # + queryOrders(date): List<Order>
    def queryOrders(self, date: str = "") -> List[Order]:
        orders = self.db.all("orders")
        if not date:
            return orders
        return [o for o in orders if o.createTime.startswith(date)]

    # + calculateAmount(couponId): double
    def calculateAmount(self, couponId: str) -> float:
        coupon = self.db.get("coupons", couponId)
        return coupon.discountValue if coupon else 0.0

    def create_cart(self, customerId: str) -> ShoppingCart:
        cart = ShoppingCart(customerId)
        self.db.save("carts", cart.cartId, cart)
        return cart

    def add_coupon(self, coupon: Coupon) -> str:
        self.db.save("coupons", coupon.couponId, coupon)
        return coupon.couponId
