"""领域实体模型汇总。"""

from .user import User, Customer, Admin, Role, Permission
from .sales import Order, OrderItem, ShoppingCart, CartItem, Payment, Coupon
from .inventory import (
    FruitCategory, Fruit, InventoryRecord, PurchaseOrder, PurchaseOrderItem,
)
from .supplier import Supplier
from .report import SalesReport, InventoryReport

__all__ = [
    "User", "Customer", "Admin", "Role", "Permission",
    "Order", "OrderItem", "ShoppingCart", "CartItem", "Payment", "Coupon",
    "FruitCategory", "Fruit", "InventoryRecord", "PurchaseOrder", "PurchaseOrderItem",
    "Supplier",
    "SalesReport", "InventoryReport",
]
