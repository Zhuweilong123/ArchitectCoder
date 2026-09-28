"""销售领域模型（对应 Sales Domain 类图）。"""
from __future__ import annotations

from typing import List, Optional

from ..common import CouponStatus, OrderStatus, PaymentStatus, new_id, now_str


class OrderItem:
    """订单明细：下单时快照水果名称与单价，防止后续改价影响历史订单。"""

    def __init__(self, orderId: str, fruitId: str, fruitName: str, unitPrice: float, quantity: int):
        self.itemId = new_id("OI")
        self.orderId = orderId
        self.fruitId = fruitId
        self.fruitName = fruitName
        self.unitPrice = unitPrice
        self.quantity = quantity
        self.subtotal = 0.0
        self.calcSubtotal()

    # + calcSubtotal(): double
    def calcSubtotal(self) -> float:
        self.subtotal = round(self.unitPrice * self.quantity, 2)
        return self.subtotal


class Order:
    """订单：状态机 待支付->已支付->已发货->已完成/已取消。"""

    def __init__(self, customerId: str):
        self.orderId = new_id("O")
        self.orderNo = "NO" + self.orderId[1:]
        self.customerId = customerId
        self.totalAmount = 0.0
        self.discountAmount = 0.0
        self.payAmount = 0.0
        self.status = OrderStatus.PENDING_PAYMENT
        self.createTime = now_str()
        self.payTime = ""
        self.remark = ""
        self.items: List[OrderItem] = []
        self.payment: Optional["Payment"] = None
        self.coupon: Optional["Coupon"] = None

    def add_item(self, item: OrderItem) -> None:
        self.items.append(item)

    # + calculateAmount(coupon: Coupon): double
    def calculateAmount(self, coupon: Optional["Coupon"] = None,
                        vip_discount_rate: float = 1.0) -> float:
        """计算订单金额：原价减优惠券折扣，VIP 会员按等级再打折。"""
        total = round(sum(i.subtotal for i in self.items), 2)
        discount = 0.0
        if coupon is not None and coupon.validate(total):
            if coupon.discountType == "CASH":
                discount = coupon.discountValue
            else:  # DISCOUNT 折扣券，discountValue 为折扣系数，如 0.9
                discount = total * (1 - coupon.discountValue)
        after_coupon = max(0.0, total - discount)
        vip_discount = after_coupon * (1 - vip_discount_rate)
        self.totalAmount = total
        self.discountAmount = round(discount + vip_discount, 2)
        self.payAmount = round(max(0.0, total - self.discountAmount), 2)
        return self.payAmount

    # + updateStatus(status: int): void
    def updateStatus(self, status) -> None:
        self.status = OrderStatus(status)

    # + cancel(): bool
    def cancel(self) -> bool:
        if self.status in (OrderStatus.PENDING_PAYMENT, OrderStatus.PAID):
            self.status = OrderStatus.CANCELLED
            return True
        return False


class ShoppingCart:
    """购物车：每个顾客一个购物车，结算后清空。"""

    def __init__(self, customerId: str):
        self.cartId = new_id("C")
        self.customerId = customerId
        self.items: List["CartItem"] = []

    # + addItem(fruitId, quantity): void
    def addItem(self, fruitId: str, quantity: int, fruitName: str = "", unitPrice: float = 0.0) -> None:
        for it in self.items:
            if it.fruitId == fruitId:
                it.quantity += quantity
                it.calcSubtotal()
                return
        self.items.append(CartItem(self.cartId, fruitId, fruitName, unitPrice, quantity))

    # + removeItem(itemId: string): void
    def removeItem(self, itemId: str) -> None:
        self.items = [i for i in self.items if i.cartItemId != itemId]

    # + updateQuantity(itemId, quantity): void
    def updateQuantity(self, itemId: str, quantity: int) -> None:
        for it in self.items:
            if it.cartItemId == itemId:
                it.quantity = quantity
                it.calcSubtotal()

    # + clear(): void
    def clear(self) -> None:
        self.items = []

    def total(self) -> float:
        return round(sum(i.subtotal for i in self.items), 2)


class CartItem:
    """购物车明细项。"""

    def __init__(self, cartId: str, fruitId: str, fruitName: str, unitPrice: float, quantity: int):
        self.cartItemId = new_id("CI")
        self.cartId = cartId
        self.fruitId = fruitId
        self.fruitName = fruitName
        self.unitPrice = unitPrice
        self.quantity = quantity
        self.subtotal = 0.0
        self.calcSubtotal()

    # + calcSubtotal(): double
    def calcSubtotal(self) -> float:
        self.subtotal = round(self.unitPrice * self.quantity, 2)
        return self.subtotal


class Payment:
    """支付记录：支持微信/支付宝/余额，退款需原路退回。"""

    def __init__(self, orderId: str, payType: str, amount: float):
        self.paymentId = new_id("P")
        self.orderId = orderId
        self.payType = payType
        self.amount = amount
        self.status = PaymentStatus.PENDING
        self.transactionNo = ""
        self.payTime = ""

    # + pay(orderId, payType, amount): bool
    def pay(self, orderId: str, payType: str, amount: float) -> bool:
        self.orderId = orderId
        self.payType = payType
        self.amount = amount
        self.status = PaymentStatus.SUCCESS
        self.transactionNo = new_id("TXN")
        self.payTime = now_str()
        return True

    # + refund(paymentId): bool
    def refund(self, paymentId: str = "") -> bool:
        if self.status == PaymentStatus.SUCCESS:
            self.status = PaymentStatus.REFUNDED
            return True
        return False

    # + queryStatus(): string
    def queryStatus(self) -> str:
        return self.status.name


class Coupon:
    """优惠券：满减或折扣券，需满足最低消费金额且未过期。"""

    def __init__(self, couponId: str, customerId: str, couponName: str, discountType: str,
                 discountValue: float, minAmount: float, validFrom: str = "", validUntil: str = ""):
        self.couponId = couponId
        self.customerId = customerId
        self.couponName = couponName
        self.discountType = discountType      # CASH=满减, DISCOUNT=折扣
        self.discountValue = discountValue
        self.minAmount = minAmount
        self.status = CouponStatus.UNUSED
        self.validFrom = validFrom
        self.validUntil = validUntil

    # + validate(orderAmount: double): bool
    def validate(self, orderAmount: float) -> bool:
        return self.status == CouponStatus.UNUSED and orderAmount >= self.minAmount

    # + use(): void
    def use(self) -> None:
        self.status = CouponStatus.USED
