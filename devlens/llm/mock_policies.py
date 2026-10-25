"""角色级 Mock 策略：按场景编写确定性的「模型行为脚本」。

每个策略模拟真实模型在该角色下的行为轨迹：
    - 何时调用什么工具（从对话历史里的工具返回决定下一步，与真实 ReAct 同构）；
    - 最终产出什么结构的结论（归因报告 / 评审意见 / 失败归因）。

关键点：策略并非纯硬编码——它会解析工具的真实返回（搜索命中、日志内容、
diff 内容），动态决定下一步调用与结论引用，因此工具系统的任何行为变化
都会如实反映到最终产出中。
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field
from typing import Any

from devlens.llm.base import ChatMessage, LLMResponse, ToolCall
from devlens.llm.mock import MockPolicy

_counter = itertools.count(1)

EVIDENCE_MARK = "EVIDENCE |"
VERDICT_MARK = "VERDICT_JSON"


def _resp(
    content: str | None = None,
    tool: str | None = None,
    args: dict[str, Any] | None = None,
) -> LLMResponse:
    if tool:
        return LLMResponse(
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(id=f"mock_{next(_counter):04d}", name=tool, arguments=args or {})
            ],
        )
    return LLMResponse(content=content or "")


def _tool_outputs(messages: list[ChatMessage]) -> list[tuple[str, str]]:
    """按顺序返回 (tool_name, output_text)。"""
    out: list[tuple[str, str]] = []
    for m in messages:
        if m.role == "tool":
            out.append((m.name or "", m.content or ""))
    return out


def _call_counts(messages: list[ChatMessage]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in messages:
        for tc in m.tool_calls or []:
            counts[tc.name] = counts.get(tc.name, 0) + 1
    return counts


def _last_output_of(messages: list[ChatMessage], name: str) -> str:
    for tool, content in reversed(_tool_outputs(messages)):
        if tool == name:
            return content
    return ""


def _first_path(text: str, ext: str = ".py") -> str | None:
    m = re.search(rf"([\w./\\-]+{re.escape(ext)})", text)
    return m.group(1) if m else None


def _extract_identifiers(text: str) -> list[str]:
    """从问题描述中抽取代码标识符（模拟真实模型读 issue 的能力）。"""
    ids = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", text)
    seen: list[str] = []
    for i in ids:
        if i.lower() not in {i2.lower() for i2 in seen} and i.lower() not in {
            "http", "https", "com", "www", "error", "traceback", "return", "def",
        }:
            seen.append(i)
    return seen[:3]


def _evidence(path: str, line: int | str, quote: str) -> str:
    return f"{EVIDENCE_MARK} {path} | {line} | {quote.strip()[:160]}"


@dataclass
class ScenarioContext:
    """场景数据：由工作流入参派生，注入到各策略。"""

    scenario: str  # issue | pr | ci
    input: dict[str, Any] = field(default_factory=dict)

    @property
    def task_id(self) -> str:
        return str(self.input.get("id", "TASK"))

    @property
    def title(self) -> str:
        return str(self.input.get("title", ""))

    @property
    def body(self) -> str:
        return str(self.input.get("body", "") or self.input.get("description", ""))


# ---------------------------------------------------------------- Planner
class PlannerPolicy(MockPolicy):
    """规划者：不调用工具，直接输出任务拆解。"""

    def __init__(self, ctx: ScenarioContext) -> None:
        self.ctx = ctx

    async def respond(self, agent, messages, tools) -> LLMResponse:
        sid = self.ctx.task_id
        plan = f"""## 任务拆解（{sid}）
目标：处理「{self.ctx.title}」

1. 证据检索（researcher）：定位相关代码、日志与项目文档，收集可引用的证据
2. 结论生成（analyst）：基于证据链产出结构化结论，每条结论必须挂证据
3. 质量评审（critic）：校验证据充分性与结论完整性，不达标打回重做

约束：结论必须可追溯（file:line），禁止无证据推断。"""
        return _resp(content=plan)


# ---------------------------------------------------------------- Researcher
class IssueResearcherPolicy(MockPolicy):
    """Issue 场景的检索者：搜索代码 -> 读源码 -> 查知识库 -> 汇总证据。"""

    def __init__(self, ctx: ScenarioContext) -> None:
        self.ctx = ctx
        ids = _extract_identifiers(self.ctx.title + " " + self.ctx.body)
        # 优先按「def <标识符>」精确搜索，直接命中定义处（模拟模型选择搜索词的判断）
        self.pattern = f"def {ids[0]}" if ids else "def |class "
        self.keyword = ids[0] if ids else "def"
        self.kb_query = "优惠券 折扣 金额 负数 历史故障"

    async def respond(self, agent, messages, tools) -> LLMResponse:
        counts = _call_counts(messages)
        if not counts:
            return _resp(tool="search_code", args={"pattern": self.pattern})
        if "read_file" not in counts:
            hits = _last_output_of(messages, "search_code")
            path = _first_path(hits) or "app/services/order_service.py"
            return _resp(tool="read_file", args={"path": path})
        if "search_docs" not in counts:
            return _resp(tool="search_docs", args={"query": self.kb_query})
        return _resp(content=self._conclude(messages))

    def _conclude(self, messages: list[ChatMessage]) -> str:
        lines = [f"## 检索结论（{self.ctx.task_id}）"]
        evidence: list[str] = []

        read_out = _last_output_of(messages, "read_file")
        if read_out:
            path = _first_path(read_out) or "unknown"
            hit = next(
                (
                    ln
                    for ln in read_out.splitlines()
                    if self.keyword.lower() in ln.lower() and "|" in ln
                ),
                None,
            )
            if hit:
                lineno, code = hit.split("|", 1)
                lines.append(f"- 疑似问题代码：{path.strip()} 第 {lineno.strip()} 行附近")
                evidence.append(_evidence(path.strip(), lineno.strip(), code))
            else:
                lines.append(f"- 已阅读 {path.strip()}，未发现直接命中行")

        docs_out = _last_output_of(messages, "search_docs")
        if docs_out and "无相关内容" not in docs_out:
            first_block = docs_out.split("\n\n")[0]
            source = "kb"
            text = first_block
            if "来源:" in first_block:
                source = first_block.split("来源:")[-1].split("\n")[0].split(" (")[0].strip()
                text = first_block.split("\n", 1)[-1]
            lines.append(f"- 知识库命中历史记录：{source}")
            evidence.append(_evidence(f"kb:{source}", 0, text))

        if not evidence:
            lines.append("- 未收集到有效证据")
        return "\n".join(lines) + "\n" + "\n".join(evidence)


# ---------------------------------------------------------------- Analyst
class IssueAnalystPolicy(MockPolicy):
    """Issue 场景的分析者：先精读源码，再基于证据链输出归因报告。"""

    def __init__(self, ctx: ScenarioContext) -> None:
        self.ctx = ctx

    def _code_evidence_path(self, background: str) -> str:
        for ln in (background or "").splitlines():
            if ln.startswith(EVIDENCE_MARK) and ".py" in ln:
                m = re.match(rf"{re.escape(EVIDENCE_MARK)}\s*(\S+) \|", ln)
                if m:
                    return m.group(1)
        return "app/utils/discount.py"

    async def respond(self, agent, messages, tools) -> LLMResponse:
        counts = _call_counts(messages)
        if not counts:
            background = messages[1].content if len(messages) > 1 else ""
            path = self._code_evidence_path(background)
            return _resp(tool="read_file", args={"path": path})
        return _resp(content=self._report(messages))

    def _report(self, messages: list[ChatMessage]) -> str:
        background = messages[1].content if len(messages) > 1 else ""
        evid = [ln for ln in (background or "").splitlines() if ln.startswith(EVIDENCE_MARK)]

        root_cause = "未定位到明确根因（证据不足）"
        fix = "需要补充代码定位后重新分析"
        code_evid = next((e for e in evid if ".py" in e), None)
        doc_evid = next((e for e in evid if ".py" not in e), None)

        read_out = _last_output_of(messages, "read_file")
        if read_out:
            path = _first_path(read_out) or (code_evid or "app/utils/discount.py")
            # 启发式定位可疑行：折扣计算语句（含 value 与 return 的第一行）
            for lineno_line in read_out.splitlines():
                if "|" in lineno_line and "coupon.value" in lineno_line and "return" in lineno_line:
                    lineno, quote = lineno_line.split("|", 1)
                    if "(1 - coupon.value" in quote:
                        root_cause = (
                            f"{path}:{lineno.strip()} —— 百分比折扣计算把「百分数」当「小数」使用："
                            f"`{quote.strip()}`。coupon.value=30 表示减 30%，却被代入 1-30，"
                            "计算结果被放大为负数。"
                        )
                    else:
                        root_cause = f"{path}:{lineno.strip()} —— `{quote.strip()}`"
                    fix = (
                        "改为 price * (1 - coupon.value / 100.0)；"
                        "同时为百分比券补充边界单测（value=0/30/100）。"
                    )
                    evid.insert(0, _evidence(path, lineno.strip(), quote))
                    break

        confidence = "high" if code_evid and doc_evid else ("low" if not evid else "medium")
        report = f"""## 归因分析报告（{self.ctx.task_id}）
**问题**：{self.ctx.title}

**根因**：{root_cause}

**证据链**：
{chr(10).join(f'- {e}' for e in evid) if evid else '- （无）'}

**影响范围**：下单与优惠券核销链路，用户侧表现为订单金额异常。

**修复建议**：{fix}

**预防措施**：在金额计算模块引入「单位不变量」校验与属性测试，防止百分数/小数混用。

CONFIDENCE: {confidence}"""
        return report


# ---------------------------------------------------------------- Reviewer
class PRReviewerPolicy(MockPolicy):
    """PR 评审者：看 diff -> 读变更文件 -> 输出结构化评审意见。"""

    def __init__(self, ctx: ScenarioContext) -> None:
        self.ctx = ctx

    async def respond(self, agent, messages, tools) -> LLMResponse:
        counts = _call_counts(messages)
        if not counts:
            base = str(self.ctx.input.get("base") or "main")
            return _resp(tool="git_diff", args={"base": base})
        if "read_file" not in counts:
            diff = _last_output_of(messages, "git_diff")
            files = re.findall(r"^\+\+\+ b/(\S+\.py)$", diff, flags=re.M)
            path = files[0] if files else "app/services/coupon_service.py"
            return _resp(tool="read_file", args={"path": path})
        return _resp(content=self._review(messages))

    def _review(self, messages: list[ChatMessage]) -> str:
        diff = _last_output_of(messages, "git_diff")
        read_out = _last_output_of(messages, "read_file")
        files = sorted(set(re.findall(r"^\+\+\+ b/(\S+)$", diff, flags=re.M))) or ["未知"]
        evidence: list[str] = []
        issues: list[str] = []

        if read_out:
            path = _first_path(read_out) or files[0]
            read_lines = read_out.splitlines()
            for idx, lineno_line in enumerate(read_lines):
                if "|" not in lineno_line:
                    continue
                lineno, code = lineno_line.split("|", 1)
                # 吞异常：except Exception 与 pass 常在相邻两行
                if "except Exception" in code or "except Exception" in lineno_line:
                    next_line = read_lines[idx + 1] if idx + 1 < len(read_lines) else ""
                    if "pass" in next_line:
                        issues.append(
                            f"1. [严重] {path}:{lineno.strip()} —— `except Exception: pass` "
                            "静默吞掉所有异常：优惠券服务故障会被掩盖，下游拿到错误数据继续下单。"
                            "至少应记录日志并返回明确错误。"
                        )
                        evidence.append(_evidence(path, lineno.strip(), f"{code.strip()} / {next_line.strip()}"))
                if "timeout" in lineno_line.lower() and " 3" in lineno_line and "=" in lineno_line:
                    issues.append(
                        f"2. [中等] {path}:{lineno.strip()} —— 超时从 30s 调整为 3s，"
                        "缺少压测数据支撑，弱网环境下可能出现批量超时。"
                    )
                    evidence.append(_evidence(path, lineno.strip(), code))

        if not issues:
            issues.append("1. [低] 未发现高风险变更，建议关注测试覆盖。")

        review = f"""## PR 评审意见（{self.ctx.task_id}）
**变更摘要**：{', '.join(files)}

**总体风险**：{'高' if any('严重' in i for i in issues) else '中'}

**问题清单**：
{chr(10).join(issues)}

**测试建议**：为异常路径补充单测（服务不可用/超时场景），断言行为是「重试或显式失败」而非静默吞错。

**结论**：{'Request Changes' if any('严重' in i for i in issues) else 'Approve with comments'}

CONFIDENCE: high"""
        return review + "\n" + "\n".join(evidence)


# ---------------------------------------------------------------- Troubleshooter
class CITroubleshooterPolicy(MockPolicy):
    """CI 排障者：读失败日志 -> 定位源码 -> 输出失败归因。"""

    def __init__(self, ctx: ScenarioContext) -> None:
        self.ctx = ctx

    async def respond(self, agent, messages, tools) -> LLMResponse:
        counts = _call_counts(messages)
        if not counts:
            return _resp(
                tool="read_log",
                args={
                    "path": str(self.ctx.input.get("log") or "fixtures/logs/ci_JOB-88.log"),
                    "filter": "ERROR|FAILED|AssertionError|assert",
                },
            )
        if "read_file" not in counts:
            log = _last_output_of(messages, "read_log")
            src = _first_path(
                "\n".join(ln for ln in log.splitlines() if "app/" in ln or "utils/" in ln)
            ) or "app/utils/discount.py"
            return _resp(tool="read_file", args={"path": src})
        return _resp(content=self._conclude(messages))

    def _conclude(self, messages: list[ChatMessage]) -> str:
        log = _last_output_of(messages, "read_log")
        read_out = _last_output_of(messages, "read_file")
        failed_test = next(iter(re.findall(r"(tests/[\w/]+\.py::\w+)", log)), "未知用例")
        evidence: list[str] = []

        # 日志证据：断言失败的原始输出
        log_path = str(self.ctx.input.get("log") or "fixtures/logs/ci_JOB-88.log")
        log_hit = next(
            (ln for ln in log.splitlines() if "assert" in ln.lower() and "|" in ln), None
        )
        if log_hit:
            lineno, text = log_hit.split("|", 1)
            evidence.append(_evidence(log_path, lineno.strip(), text))

        code_loc = "未知位置"
        if read_out:
            path = _first_path(read_out) or "app/utils/discount.py"
            for lineno_line in read_out.splitlines():
                if "coupon.value" in lineno_line and "|" in lineno_line and "return" in lineno_line:
                    lineno, code = lineno_line.split("|", 1)
                    code_loc = f"{path}:{lineno.strip()}"
                    evidence.append(_evidence(path, lineno.strip(), code))
                    break

        cause = (
            f"失败用例 `{failed_test}` 断言折扣金额不符。追溯至 {code_loc}："
            "百分比折扣把百分数当小数计算（1 - value），与单测期望 (1 - value/100) 不一致，"
            "属于单位混用类缺陷，与 ISSUE-101 为同一根因。"
        )
        report = f"""## CI 失败归因（{self.ctx.task_id}）

**流水线**：{self.ctx.input.get('pipeline', 'order-service')}（状态: {self.ctx.input.get('status', 'failed')}）

**失败用例**：{failed_test}

**根因**：{cause}

**证据链**：
{chr(10).join(f'- {e}' for e in evidence) if evidence else '- （无）'}

**修复建议**：修正 {code_loc} 的折扣公式为 `price * (1 - coupon.value / 100.0)`，合并修复后重跑流水线。

**关联**：ISSUE-101（同一根因，修复后两处同步关闭）

CONFIDENCE: high"""
        return report + "\n" + "\n".join(evidence)


# ---------------------------------------------------------------- Critic
class CriticPolicy(MockPolicy):
    """质量评审：校验上游产出的证据充分性，不达标打回（反思回路）。"""

    def __init__(self, ctx: ScenarioContext) -> None:
        self.ctx = ctx

    async def respond(self, agent, messages, tools) -> LLMResponse:
        background = messages[1].content if len(messages) > 1 else ""
        evidence_count = (background or "").count(EVIDENCE_MARK)
        has_conclusion = any(k in (background or "") for k in ("根因", "风险", "结论", "归因"))

        if evidence_count >= 2 and has_conclusion:
            verdict = "pass"
            feedback = "证据充分（代码 + 文档/日志相互印证），结论完整，可交付。"
        elif has_conclusion:
            verdict = "revise"
            feedback = f"结论已有方向但证据不足（当前 {evidence_count} 条，要求 ≥2），请补充代码定位与知识库佐证。"
        else:
            verdict = "revise"
            feedback = "缺少结构化结论（根因/风险/建议），请按模板重写。"

        payload = {
            "verdict": verdict,
            "score": 88 if verdict == "pass" else 45,
            "evidence_count": evidence_count,
            "feedback": feedback,
        }
        return _resp(content=json.dumps({**payload, VERDICT_MARK: True}, ensure_ascii=False))


class EchoPolicy(MockPolicy):
    """兜底策略：未适配角色时返回提示文本，保证框架不因缺脚本而崩溃。"""

    async def respond(self, agent, messages, tools) -> LLMResponse:
        return _resp(content=f"[Mock] 角色 {agent} 未配置行为脚本，直接返回。")


def build_policy(role: str, scenario: str, data: dict[str, Any]) -> MockPolicy:
    """按 (角色, 场景) 构造 Mock 策略——Mock 模式下的『多模型路由』。"""
    ctx = ScenarioContext(scenario=scenario, input=data)
    if role == "planner":
        return PlannerPolicy(ctx)
    if role == "researcher":
        if scenario == "pr":
            return PRReviewerPolicy(ctx)  # PR 场景 researcher 直接由评审者承担
        if scenario == "ci":
            return CITroubleshooterPolicy(ctx)
        return IssueResearcherPolicy(ctx)
    if role == "analyst":
        return IssueAnalystPolicy(ctx)
    if role == "reviewer":
        return PRReviewerPolicy(ctx)
    if role == "troubleshooter":
        return CITroubleshooterPolicy(ctx)
    if role == "critic":
        return CriticPolicy(ctx)
    return EchoPolicy()
