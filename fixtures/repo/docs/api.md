# 下单接口文档

## POST /orders

创建订单。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| sku | string | 是 | 商品编码 |
| price | float | 是 | 商品原价（元） |
| coupon | string | 否 | 优惠券码，如 P30 |

### 优惠券语义约定

- `percent` 类：`value` 表示**折扣百分数**，30 表示减 30%（即七折）。
  折后价 = price * (1 - value / 100)。
- `full_reduction` 类：`value` 表示满减金额（元），直接从价格中扣除。

> ⚠️ 历史上多次因「百分数 / 小数 / 分」单位混用产生金额事故，
> 涉及金额计算请先阅读 docs/troubleshooting.md 的检查清单。
