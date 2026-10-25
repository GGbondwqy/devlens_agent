"""优惠券服务：券的查询与校验。"""

import httpx

from app.utils.discount import Coupon
from app.utils.logger import get_logger

logger = get_logger(__name__)

TIMEOUT = 3  # PR-42: 30 -> 3，尽快失败
COUPON_API = "https://coupon.internal.example/api/v1"


def get_coupon(code: str) -> Coupon | None:
    """查询优惠券。PR-42 增加异常兜底：任何异常都视为无券，不再向上游抛错。"""
    try:
        resp = httpx.get(f"{COUPON_API}/{code}", timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return Coupon(code=code, kind=data["kind"], value=float(data["value"]))
    except Exception:
        pass  # PR-42: 兜底，静默降级为无券
