"""DevLens Agent —— 面向企业研发流程的多智能体代码协作框架。

垂直场景：
    * Issue 分析   —— 理解问题，检索代码/日志/文档，输出归因分析
    * PR 评审      —— 读取变更 diff 与相关代码，输出风险与处理建议
    * CI 排障      —— 解析失败日志，定位代码，输出失败归因与修复建议

架构分层：
    llm/        模型网关（多 Provider 抽象 + Mock + OpenAI 兼容实现）
    tools/      工具系统（JSON Schema 声明、注册中心、沙箱、输出截断）
    core/       Agent 内核（ReAct 循环、上下文管理、事件总线）
    workflow/   自研流程编排引擎（状态机 + 条件边 + 反思回路 + 检查点）
    roles/      研发协作角色（Planner / Researcher / Analyst / Reviewer / ...）
    memory/     任务台账与会话持久化
    server/     FastAPI 服务（REST + SSE 流式事件）
    eval/       离线评测 harness（固定案例 + 期望断言 + 打分）
"""

__version__ = "0.1.0"
