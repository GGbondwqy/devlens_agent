"""折扣计算单测。"""

from app.utils.discount import Coupon, apply_discount


def test_full_reduction():
    coupon = Coupon(code="MJ20", kind="full_reduction", value=20)
    assert apply_discount(100.0, coupon) == 80.0


def test_percent_discount():
    """百分比券：value=30 表示减 30%，100 元应付 70 元。"""
    coupon = Coupon(code="P30", kind="percent", value=30)
    assert apply_discount(100.0, coupon) == 70.0


def test_no_coupon():
    assert apply_discount(99.5, None) == 99.5
