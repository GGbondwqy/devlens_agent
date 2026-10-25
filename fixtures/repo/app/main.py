"""订单服务入口（FastAPI）。"""

from fastapi import FastAPI

from app.services.order_service import create_order

app = FastAPI(title="order-service")


@app.post("/orders")
def post_order(sku: str, price: float, coupon: str | None = None):
    """下单接口：POST /orders?sku=x&price=100&coupon=P30"""
    return create_order(sku=sku, price=price, coupon_code=coupon)
