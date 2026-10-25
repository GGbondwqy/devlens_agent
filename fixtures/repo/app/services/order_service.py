"""订单服务：下单与金额计算。"""

from app.services.coupon_service import get_coupon
from app.utils.discount import Coupon, apply_discount
from app.utils.logger import get_logger

logger = get_logger(__name__)


def create_order(sku: str, price: float, coupon_code: str | None = None) -> dict:
    """创建订单；有券码时先取券，再计算折后价。"""
    coupon = get_coupon(coupon_code) if coupon_code else None
    total = apply_discount(price, coupon)
    logger.info("order created sku=%s total=%s coupon=%s", sku, total, coupon_code)
    return {"sku": sku, "price": price, "coupon": coupon_code, "total": total}
