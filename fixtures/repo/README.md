# order-service（示例业务仓库）

一个极简的「订单 + 优惠券」演示服务，作为 DevLens Agent 的靶场仓库。

结构：
- `app/main.py` —— FastAPI 入口（POST /orders）
- `app/services/order_service.py` —— 下单流程
- `app/services/coupon_service.py` —— 优惠券查询（PR-42 变更点）
- `app/utils/discount.py` —— 折扣计算（ISSUE-101 埋雷点）
- `tests/test_discount.py` —— 折扣单测（CI-JOB-88 失败点）
- `docs/` —— 接口文档与历史故障记录（知识库）

> 该仓库由 `scripts/setup_fixtures.py` 初始化为 git 仓库：
> main 为基础版，pr-42 分支包含待评审变更。
