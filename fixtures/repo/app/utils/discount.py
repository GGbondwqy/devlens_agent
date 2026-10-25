"""价格与折扣计算工具。"""

from dataclasses import dataclass


@dataclass
class Coupon:
    code: str
    kind: str      # percent | full_reduction
    value: float   # percent: 折扣百分数（30 表示减 30%）；full_reduction: 满减金额


def apply_discount(price: float, coupon: Coupon | None) -> float:
    """按优惠券计算折后价；percent 类 value 为百分数（30 = 减 30%）。"""
    if coupon is None:
        return round(price, 2)
    if coupon.kind == "percent":
        return round(price * (1 - coupon.value), 2)
    if coupon.kind == "full_reduction":
        return round(max(price - coupon.value, 0), 2)
    raise ValueError(f"未知券类型: {coupon.kind}")
