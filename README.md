# 🔍 DevLens Agent —— AI 研发协作多智能体框架

**让 AI 像一位资深研发一样：分析 Issue 归因 · 审查 Pull Request · 排查 CI 失败。**

> Multi-agent framework for the dev workflow: **issue triage, PR review, CI failure diagnosis** —
> ReAct kernel + self-built orchestration engine (state machine, reflection loop, checkpoints),
> fully offline with a mock LLM, pluggable to any OpenAI-compatible model.

[![CI](https://github.com/GGbondwqy/devlens_agent/actions/workflows/ci.yml/badge.svg)](https://github.com/GGbondwqy/devlens_agent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Tests](https://img.shields.io/badge/tests-32%20passing-brightgreen)
![Eval](https://img.shields.io/badge/offline%20eval-100%2F100-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

企业研发流程里，**分析 Issue、审查 PR、排查 CI 失败**是三类高频、高人工成本的协作工作。
DevLens Agent 把它们产品化为同一条多智能体流水线：

```
规划 Planner → 证据检索 Researcher → 结论生成 Analyst/Reviewer/Troubleshooter → 质检 Critic
                                        ↑______________ 不达标打回重做（反思回路）______________|
```

**框架先行、模型可插拔**：内置 Mock 模型即可离线跑通全流程（演示/测试零成本），
改一行配置即切换到 DeepSeek / Qwen / GLM / vLLM 等任何 OpenAI 兼容模型，业务代码零改动。

> 📖 二次开发请直接看 **[docs/开发指南.md](docs/开发指南.md)**（新增工具/角色/场景、接入真实模型与业务仓库）；
> 设计取舍见 [docs/设计文档.md](docs/设计文档.md)，调研依据见 [docs/调研报告.md](docs/调研报告.md)。

---

## 1. 核心特性

| 能力 | 说明 | 设计参考 |
|---|---|---|
| **ReAct Agent 内核** | `while 循环 + 工具调用回填`，内核不知道任何业务与具体模型 | Claude Code / OpenHands |
| **自研流程编排引擎** | 状态机 + 条件边 + 反思回路（Critic 打回）+ 检查点续跑，语义与 LangGraph 对齐 | LangGraph / gpt-pilot |
| **类型化工具系统** | JSON Schema 声明（与 function calling 同构）、注册中心、参数校验、输出截断 | SWE-agent 的 ACI |
| **安全沙箱** | 路径收敛（防目录穿越）+ 命令白名单（git/python/pytest）+ 超时 + 拒绝审计 | Copilot coding agent |
| **证据优先协议** | 结论强制挂 `EVIDENCE \| 文件 \| 行号 \| 原文` 证据，Critic 按证据充分性质检，对抗幻觉 | DevFlow AI 类产品 |
| **上下文管理** | token 预算 + 消息裁剪 + 摘要占位（摘要必须更小，否则退化计数占位） | OpenHands Condenser |
| **多模型网关** | 按角色路由不同模型（成本/能力分层）；Mock / OpenAI 兼容双实现 | — |
| **知识库检索** | 零依赖 TF-IDF 文档 RAG，接口与向量检索同构，可平滑换 PGVector | — |
| **事件溯源** | 全链路类型化事件 + SSE 流式 + `events.jsonl` 落盘回放 | OpenHands event stream |
| **离线评测 harness** | 固定案例 + 结构断言 + 打分报告，三场景 100 分（CI 强制） | 企业 PR 回放评测思路 |

## 2. 快速开始

```bash
# 环境要求：Python ≥ 3.10；git（仅 PR 场景需要）
pip install -e ".[dev]"            # 安装依赖
python scripts/setup_fixtures.py   # 初始化靶场仓库（示例仓库 + 埋雷 bug + 待评审 PR 分支）

# 三个研发场景（Mock 模型，无需 API Key）
python -m devlens.cli run issue --input fixtures/issues/ISSUE-101.json   # ① Issue 归因分析
python -m devlens.cli run pr    --input fixtures/prs/PR-42.json          # ② PR 代码评审
python -m devlens.cli run ci    --input fixtures/ci/JOB-88.json          # ③ CI 失败排查

python -m devlens.cli eval          # 离线评测（三场景打分，CI 强制 100 分）
python -m devlens.cli serve         # Web 控制台（SSE 实时事件流）→ http://127.0.0.1:8000
python -m devlens.cli tasks         # 任务台账    python -m devlens.cli replay <task_id>
python -m pytest tests/ -q          # 32 个测试（沙箱/工具/上下文/内核/引擎/端到端）
```

## 3. 实际运行效果

**① Issue 归因分析**（自动检索代码 + 知识库，定位到埋雷代码行）：

```markdown
## 归因分析报告（ISSUE-101）
**根因**：app/utils/discount.py:18 —— 百分比折扣计算把「百分数」当「小数」使用：
`return round(price * (1 - coupon.value), 2)`。coupon.value=30 表示减 30%，
却被代入 1-30，计算结果被放大为负数。
**证据链**：discount.py:18（代码） + docs/troubleshooting.md 历史故障（知识库）
**修复建议**：改为 price * (1 - coupon.value / 100.0)，并补充边界单测
```

**② PR 代码评审**（基于真实 git diff + 文件上下文）：

```markdown
1. [严重] app/services/coupon_service.py:21 —— `except Exception: pass` 静默吞掉所有异常
2. [中等] app/services/coupon_service.py:10 —— 超时从 30s 调整为 3s，缺少压测数据支撑
**结论**：Request Changes
```

**③ CI 失败排查**（日志 → 堆栈 → 源码 → 归因，并关联同根因 Issue）：

```markdown
**失败用例**：tests/test_discount.py::test_percent_discount
**根因**：discount.py:18 与单测期望 (1 - value/100) 不一致 —— 与 ISSUE-101 同根因
```

每个任务在 `.runs/<task_id>/` 留下完整痕迹：`result.md`（最终报告）、`state.json`（检查点，
支持 `--resume` 续跑）、`events.jsonl`（事件流水，支持 `replay` 回放）、`summary.json`（台账）。

## 4. 架构一览

```
CLI (argparse+rich)          Web (FastAPI + SSE + 单页控制台)
            └────────────┬────────────┘
                  TaskRunner 组装层
                         │
        FlowEngine 流程编排（状态机 + 反思回路 + 检查点）
           plan → research → analyze/review/troubleshoot → critic
                     ↑____________ revise（证据不足打回）____________|
                         │
                  BaseAgent ReAct 内核（Think → Act → Observe）
                         │
        ┌────────────────┴───────────────┐
   LLM 网关（按角色路由模型）        ToolRegistry + Sandbox
   Mock / OpenAI 兼容双实现     10 个工具：读写文件/搜索/git/日志/KB/命令
        └──── EventBus 事件溯源（CLI 渲染 / SSE / jsonl 落盘）────┘
```

三个场景复用同一套引擎，差异只在「角色工具面 + 系统提示词 + 流程图」——
新增场景（如单测生成）只需加角色规格与图定义，引擎零改动，这正是框架层价值的证明。

## 5. 目录结构（逐文件说明）

```
devlens/
├── core/
│   ├── agent.py          # ★ ReAct 循环内核：Think→Act→Observe→Final
│   ├── context.py        # token 估算 + 消息裁剪 + 摘要（保底策略）
│   └── events.py         # 事件总线 + 类型化事件 + jsonl 落盘
├── llm/
│   ├── base.py           # LLMProvider 抽象 / ChatMessage / ToolCall / LLMResponse
│   ├── mock.py           # Mock 模型后端（策略注入式）
│   ├── mock_policies.py  # ★ 各角色确定性策略（解析工具真实返回，与真实 ReAct 同构）
│   ├── openai_compatible.py # OpenAI 兼容后端（预留实现，未联调真实 Key）
│   └── router.py         # 多模型网关：按角色路由模型
├── tools/
│   ├── base.py           # ★ Tool 抽象 + ToolRegistry（校验/截断/异常转换）
│   ├── sandbox.py        # ★ 路径收敛 + 命令白名单 + 命令执行
│   ├── file_tools.py     # read_file / write_file / list_dir / search_code
│   ├── git_tools.py      # git_diff / git_log / git_branch
│   ├── log_tools.py      # read_log（tail + 过滤）
│   ├── kb_tools.py       # search_docs + DocKnowledgeBase（TF-IDF，可换向量库）
│   ├── shell_tool.py     # run_command（走沙箱白名单）
│   └── factory.py        # 组装默认工具注册表
├── roles/
│   └── specs.py          # ★ 六个角色的系统提示词 / 工具白名单 / 步数预算
├── workflow/
│   ├── engine.py         # ★ FlowEngine 状态机 + FlowGraph/FlowNode/FlowState + 检查点
│   └── graphs.py         # ★ 三大场景的流程图（含反思回路定义）
├── memory/local.py       # RunsStore：任务台账与产物持久化
├── server/app.py         # FastAPI：REST + SSE 事件流 + 控制台托管
├── eval/harness.py       # 离线评测 harness（案例 + 断言 + 打分）
├── runner.py             # TaskRunner：组装入口（CLI/Web 唯一调用点）
└── cli.py                # 命令行：run / eval / serve / tasks / replay
fixtures/                 # 靶场：示例仓库(埋雷 bug) + Issue/PR/CI 数据 + CI 日志 + 知识库
web/index.html            # 一页式控制台（EventSource 消费 SSE）
tests/                    # 32 个测试（全部离线）
scripts/setup_fixtures.py # 靶场 git 仓库初始化（main + pr-42 分支，幂等）
docs/                     # 调研报告 / 设计文档 / 开发指南（二次开发手册）/ 面试指南
config/settings.yaml      # 全局配置（模型/沙箱/上下文/流程预算），环境变量可覆盖
```

## 6. 切换真实模型

```yaml
# config/settings.yaml
llm:
  provider: openai_compatible              # mock → openai_compatible
  openai_compatible:
    base_url: https://api.deepseek.com/v1  # DeepSeek/Qwen/GLM/vLLM/Ollama 均可
    api_key: ""                            # 或环境变量 DELENS_API_KEY
```

Agent 循环、工具协议（function calling 格式）、编排引擎零改动。
更多接入方式（按角色路由模型、接入自己的业务仓库、检索升级）见 [开发指南 §3.4-3.6](docs/开发指南.md)。

## 7. FAQ

<details>
<summary><b>Mock 模型是不是"假跑"？</b></summary>

不是。Mock 策略解析的是工具的**真实返回**（搜索命中、日志内容、git diff）来决定下一步，
与真实 ReAct 行为同构——工具系统、沙箱、日志格式、知识库的任何变化都会如实反映到报告里。
它验证的是框架本身的正确性；模型只是这条流水线上的可替换组件。
</details>

<details>
<summary><b>没有装 git 能跑吗？</b></summary>

能。只有 PR 评审场景依赖 git diff。装好 git 后执行 `python scripts/setup_fixtures.py --force` 重建靶场仓库即可。
</details>

<details>
<summary><b>Windows 控制台乱码？</b></summary>

CLI 已强制 UTF-8 输出；如终端仍乱码，执行 `chcp 65001` 或使用 Windows Terminal。
</details>

<details>
<summary><b>能接我自己的公司仓库吗？</b></summary>

可以。任务输入 JSON 的 <code>repo</code> 字段指向任意本地 git 仓库（沙箱自动以它为工作区），
知识库自动加载其中的 docs/。在线仓库（webhook 触发）在 Roadmap 中，集成切入点是
<code>TaskRunner.run_task()</code>。
</details>

## 8. Roadmap

- [ ] 真实模型联调（OpenAI 兼容实现已就位，待 Key 联调）
- [ ] 向量检索替换 TF-IDF（接口同构，见开发指南 §3.6）
- [ ] GitHub/GitLab webhook 接入（PR 评审机器人化）
- [ ] 单测生成场景（第四场景，引擎零改动，见开发指南 §3.3）
- [ ] LangGraph 适配层（FlowGraph 语义对齐 StateGraph，可平移）

## 9. License

[MIT](LICENSE) © 2026 GGbondwqy —— 欢迎 Fork、二次开发与 PR。
