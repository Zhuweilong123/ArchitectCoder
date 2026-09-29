"""公共类型、状态枚举与工具函数。

对应设计中的通用概念：订单/支付/采购/用户/水果/优惠券状态码，以及 ID 生成。
"""
from __future__ import annotations

import itertools
from datetime import datetime
from enum import IntEnum


class OrderStatus(IntEnum):
    """订单状态机：待支付->已支付->已发货->已完成/已取消。"""

    PENDING_PAYMENT = 0
    PAID = 1
    SHIPPED = 2
    COMPLETED = 3
    CANCELLED = 4


class PaymentStatus(IntEnum):
    PENDING = 0
    SUCCESS = 1
    REFUNDED = 2


class PurchaseStatus(IntEnum):
    """采购单状态：待到货->已到货/已取消。"""

    PENDING_ARRIVAL = 0
    ARRIVED = 1
    CANCELLED = 2


class UserStatus(IntEnum):
    DISABLED = 0
    ACTIVE = 1


class FruitStatus(IntEnum):
    OFF_SHELF = 0
    ON_SALE = 1


class CouponStatus(IntEnum):
    UNUSED = 0
    USED = 1
    EXPIRED = 2


class StockRecordType:
    """出入库记录类型。"""

    IN = "IN"
    OUT = "OUT"


_counter = itertools.count(1)


def new_id(prefix: str) -> str:
    """生成简单的自增业务 ID。"""

    return f"{prefix}{next(_counter):06d}"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")
