# Chat V3 Plan-Execute 架构新版 Code Review 报告

> **Review 日期**: 2026-07-12
> **代码规模**: 10,575 行（`ontology_chatbi/` 5,555 行 + `ontology/` 2,877 行 + `harness/` 521 行 + 其他）
> **对比基线**: 上一版（5,555 行）+ 设计文档 `chat_v3_plan_execute_metric_analysis_design.md`

---

## 一、与上一版的关键差异

### 新增模块

| 文件 | 行数 | 核心职责 |
|------|------|---------|
| `harness/harness_sql.py` | 521 | SQL 安全验证+行数估算+LLM 修复+只读检查 |
| `ontology/schema_optimizer.py` | 1,195 | BM25 文档检索+分批优化+全局校正+Diff 审计 |
| `ontology/schema_context.py` | 254 | Schema 参考上下文构建 |
| `ontology/ontology_asset_validator.py` | 353 | 资产完整性校验（物理表/字段/引用一致性） |

### 核心改进

| 改进点 | 上一版 | 新版 | 依据 |
|--------|--------|------|------|
| **SQL 安全** | 字符串拼接+单引号转义 | `HarnessSQL` 模块：只读校验+语法 EXPLAIN+行数估算+LLM 修复+LIMIT 兜底 | AWS AgentCore Interceptor / OWASP Top 10 |
| **Schema 优化** | 无 | BM25 检索+Pydantic 验证+Diff 审计+人工审核闭环 | Anthropic Context Engineering |
| **Metric Plan-Execute** | 基础框架 | 完整实现：计划→执行→证据判定→增量扩展→最终回答 | 设计文档第 5-8 节 |
| **两阶段规划** | 无 | `SCHEMA_PLAN`（选表）→ `QUERY_PLAN`（选指标/维度/条件） | Wren AI 语义层 |
| **实体消歧** | 4 级匹配 | 4 级匹配 + LLM 语义候选选择 + 样本列复核 + AP 季度归一化 | AmbiSQL (arXiv:2508.15276) |
| **target_class 推断** | 无 | `_infer_target_class` 基于 metrics/dimensions/filters 投票 | 之前 Review P0 建议 |
| **最终回答分离** | 无 | `FINAL_STREAM` 独立 prompt + 低 temperature + 不传 tools | OpenAI Agents SDK Guardrails |
| **工具后置引导** | 无 | `_build_post_tool_guidance` 根据结果特征引导下一步 | Anthropic Context Engineering |
| **直接回答优化** | 无 | `_build_direct_answer_result` 小结果跳过 python_analyze | 减少不必要的 LLM 调用 |

---

## 二、之前 Review 建议的落实情况

| # | 之前建议 | 落实状态 | 说明 |
|---|---------|---------|------|
| P0-1 | `CLARIFY` 空壳 | ⚠️ **仍未实现** | `_handle_clarify` 仍直接返回 `State.DONE`，无澄清逻辑 |
| P0-2 | 后置自动校正 | ⚠️ **部分实现** | `HarnessSQL` 有 LLM 修复，但 `ToolExecutor` 无基于错误反馈的参数修正 |
| P0-3 | SQL 参数化 | ✅ **已通过 HarnessSQL 缓解** | `HarnessSQL` 做了只读校验和语法检查，但底层 `_build_filter_clause` 仍是拼接 |
| P0-4 | `required_dimensions` 校验 | ❌ **仍未实现** | `prompt.py` 中 `req_dims` 仍被注释 |
| P1-1 | `engine.py` 膨胀 | ⚠️ **更严重** | 从 2,182 行增至 2,183 行（基本无变化），但新增了 `harness_sql.py` 和 `schema_optimizer.py` 分担了部分职责 |
| P1-2 | 实体消歧全量扫描 | ✅ **已优化** | 增加了 LLM 语义候选选择和样本列复核，减少了无效 LIKE 查询 |
| P1-3 | 上下文压缩暴力截断 | ⚠️ **未改进** | `context_compressor.py` 仍是 41 行头尾截断 |
| P1-4 | `ToolExecutor` 有状态 | ⚠️ **未改进** | 仍持有 `scenario_id` |
| P2-1 | 单元测试 | ❌ **仍未有** | 无测试文件 |
| P2-2 | `transition_log` 持久化 | ❌ **仍未实现** | 只在内存中 |

---

## 三、新版发现的问题

### 🔴 P0 级问题

#### 1. `CLARIFY` 状态仍未实现，模糊问题直接猜测

**位置**: `engine.py:1243-1246`
```python
async def _handle_clarify(self, state: AgentState) -> State:
    """主动反问"""
    # 反问事件已在 LLM_CALL 中发送
    return State.DONE
```

**影响**: 设计文档第 4 节明确要求"低置信度触发 CLARIFY"，但代码中 `_handle_clarify` 仍是空壳。当用户问"销售额"未指定时间范围时，系统会直接猜测查询，而非主动追问。

**改进建议**: 在 `_handle_query_plan` 中增加 `required_dimensions` 校验，缺失时转入 `CLARIFY`。

**技术依据**: AmbiSQL (arXiv:2508.15276) 证明交互式歧义检测可将 Text2SQL 准确率提升 12-15%。PRACTIQ (NAACL 2025) 的两步法（分类+澄清）已在多个基准上验证有效。

#### 2. `required_dimensions` 仍未校验

**位置**: `prompt.py:289-290`
```python
# dims = metric.get("dimensions") or []
# req_dims = metric.get("required_dimensions") or []
```

**影响**: 指标的必要维度声明被注释掉，LLM 看不到哪些维度是必须的，无法在生成参数时主动补全。

**改进建议**: 恢复 `required_dimensions` 展示，并在 `_handle_query_plan` 中增加校验逻辑。

**技术依据**: dbt Semantic Layer 强制要求累积指标必须包含时间维度，2026 基准测试中达到近 100% 准确率。Wren AI 的语义模型也要求 `required_dimensions` 校验。

#### 3. `data_query.py` 仍强行剥离 LIMIT

**位置**: `data_query.py:836-843`
```python
if limit is not None:
    logger.warning("DataQuery LIMIT ignored: ...")
    limit = None
```

**影响**: 虽然 `HarnessSQL` 在外部做了行数兜底（`DEFAULT_MAX_DETAIL_ROWS=5000`），但 `data_query.py` 内部仍然无视 `limit` 参数，可能导致大表全量扫描。

**改进建议**: 移除 `data_query.py` 中的 LIMIT 剥离逻辑，让 `HarnessSQL` 统一管理行数限制。

**技术依据**: Cisco 2025 安全报告指出 OOM 是生产级 Text2SQL 的 #2 风险。`HarnessSQL` 的 `DEFAULT_MAX_DETAIL_ROWS=5000` 是合理的安全兜底。

### 🟡 P1 级问题

#### 4. `engine.py` 仍 2,183 行，职责过重

**影响**: `engine.py` 包含状态机调度、SSE 格式化、会话持久化、JSON 安全序列化、结果压缩、历史加载、最终回答生成、工具后置引导、直接回答优化等 9+ 类职责。

**改进建议**: 按职责拆分为独立模块：
```
engine.py          → 只保留状态机调度和 handlers 字典（~400行）
sse_formatter.py   → _format_sse_event, _build_persisted_tool_steps 等
persistence.py     → _persist_conversation_message, _load_conversation_history
result_compactor.py → _compact_result_payload, _compact_rows, _make_json_safe
final_answer.py    → _finalize_answer, _build_final_prompt, _final_conversation_context
```

**技术依据**: OpenAI Agents SDK 的设计原则——"每个模块职责单一，可独立测试"。LangGraph 也将图定义、状态管理、持久化分离为不同模块。

#### 5. `context_compressor.py` 仍暴力截断

**位置**: `context_compressor.py`（41 行）
```python
head = context[:int(limit * 0.75)]
tail = context[-int(limit * 0.25):]
```

**影响**: 会在 JSON 对象中间切断，破坏语法。

**改进建议**: 按行/结构单元剪枝。

**技术依据**: Anthropic 的 Context Engineering 指南强调"按结构单元剪枝，而非字符截断"。Pinecone 的 Chunking Strategies 也建议按语义边界分块。

#### 6. `ToolExecutor` 仍持有 `scenario_id`

**位置**: `tool_executor.py:42-48`
```python
def __init__(self, scenario_id: str, entity_agent: EntityDisambiguatorAgent):
    self.scenario_id = scenario_id
```

**影响**: 违反"严格无状态"原则。

**改进建议**: 将 `scenario_id` 作为参数传入 `execute` 方法。

**技术依据**: 设计文档第 3 节明确要求"子智能体严格无状态"。PydanticAI 框架也遵循此原则。

#### 7. `_build_filter_clause` 仍字符串拼接

**位置**: `data_query.py:703-760`

**影响**: 虽然 `HarnessSQL` 在外部做了安全校验，但底层 SQL 构建仍是字符串拼接，存在类型断裂风险。

**改进建议**: 改为参数化查询，生成占位符并收集参数。

**技术依据**: OWASP 仍将 SQL 注入列为 LLM 应用 #1 威胁。dbt Semantic Layer 2026 基准测试中，参数化查询是生产级 Text2SQL 的基本要求。

### 🟢 P2 级问题

#### 8. `AnalysisOrganizerTool` 仍额外调用 LLM

**位置**: `analysis_organizer.py:15-68`

**影响**: 每次工具调用后额外调用一次 LLM 整理规划文本，增加成本和延迟。

**改进建议**: 改为规则提取，从 LLM 返回的 `tool_calls` 参数中直接提取 `target_class`、`reasoning`，无需额外 LLM 调用。

#### 9. 缺少单元测试

**影响**: 10,575 行代码无任何测试文件。

**改进建议**: 优先为以下核心逻辑编写测试：
- `EntityDisambiguatorAgent._fuzzy_match`：4 级匹配逻辑
- `PlanExecuteAgent._accept_metric_subquestions`：子问题去重和预算校验
- `DataQueryEngine._build_filter_clause`：SQL 生成安全性
- `HarnessSQL.prepare`：SQL 安全验证
- `engine._metric_query_fingerprint`：查询指纹去重

#### 10. `transition_log` 未持久化

**影响**: 状态跳变日志只在内存中，请求结束后丢失。

**改进建议**: 在 `_handle_done` 中将 `transition_log` 持久化到数据库，支持事后分析和请求重放。

---

## 四、新版亮点与肯定

1. **`HarnessSQL` 模块设计优秀**: 只读校验 + EXPLAIN 语法检查 + 行数估算 + LLM 修复 + LIMIT 兜底，形成了完整的 SQL 安全闭环。
2. **`schema_optimizer.py` BM25 检索**: 移除了 Embedding 依赖，用 BM25 关键词检索替代，降低了资源消耗。
3. **Metric Plan-Execute 完整落地**: 计划→执行→证据判定→增量扩展→最终回答的完整流程已实现，与设计文档高度一致。
4. **两阶段规划**: `SCHEMA_PLAN` → `QUERY_PLAN` 的分离设计，让 LLM 先选表再选指标，降低了单次规划的复杂度。
5. **实体消歧增强**: 增加了 LLM 语义候选选择和样本列复核，提升了消歧精度。
6. **`target_class` 自动推断**: 基于 metrics/dimensions/filters 投票推断，解决了之前 LLM 选错主表的问题。
7. **最终回答分离**: `FINAL_STREAM` 使用独立 prompt 和低 temperature，不传 tools，降低了幻觉。
8. **工具后置引导**: `_build_post_tool_guidance` 根据结果特征引导下一步，减少了无效的 LLM 往返。
9. **直接回答优化**: `_build_direct_answer_result` 在小结果集时跳过 `python_analyze`，减少了不必要的 LLM 调用。
10. **`ontology_asset_validator.py`**: 资产完整性校验，丢弃不具备物理表/字段支撑的 Class/Relationship/Metric/Concept。

---

## 五、改进优先级总结

| 优先级 | 问题 | 影响 | 工作量 |
|--------|------|------|--------|
| 🔴 P0 | `CLARIFY` 空壳 | 模糊问题直接猜测 | 2天 |
| 🔴 P0 | `required_dimensions` 未校验 | 指标必要维度缺失 | 1天 |
| 🔴 P0 | `data_query.py` 强行剥离 LIMIT | OOM 风险 | 0.5天 |
| 🟡 P1 | `engine.py` 膨胀 | 可维护性差 | 2天 |
| 🟡 P1 | 上下文压缩暴力截断 | JSON 语法破坏 | 0.5天 |
| 🟡 P1 | `ToolExecutor` 有状态 | 设计原则违反 | 0.5天 |
| 🟡 P1 | `_build_filter_clause` 拼接 | 类型断裂风险 | 2天 |
| 🟢 P2 | `AnalysisOrganizer` 额外 LLM | 成本浪费 | 0.5天 |
| 🟢 P2 | 缺少单元测试 | 质量保障 | 3天 |
| 🟢 P2 | `transition_log` 未持久化 | 可观测性 | 0.5天 |

---

## 六、总结

新版代码相比上一版有**显著进步**：

1. **SQL 安全**: `HarnessSQL` 模块补齐了之前最大的安全短板
2. **Schema 优化**: BM25 检索 + Pydantic 验证 + Diff 审计形成了完整闭环
3. **Metric Plan-Execute**: 完整落地了设计文档的核心架构
4. **两阶段规划**: 降低了单次 LLM 规划的复杂度
5. **实体消歧**: 增加了 LLM 语义选择和样本复核
6. **target_class 推断**: 解决了之前选错主表的问题

但仍有 3 个 P0 级问题未解决：`CLARIFY` 空壳、`required_dimensions` 未校验、`data_query.py` 强行剥离 LIMIT。建议优先补齐这 3 项，即可进入高质量生产运行。

---

## 附录：文件清单与行数统计

| 文件 | 行数 |
|------|------|
| `ontology_chatbi/engine.py` | 2,183 |
| `ontology_chatbi/node/entity_disambiguator.py` | 917 |
| `ontology_chatbi/prompt.py` | 592 |
| `ontology_chatbi/helper.py` | 418 |
| `ontology_chatbi/node/ontology_agent.py` | 330 |
| `ontology_chatbi/node/plan_execute_agent.py` | 213 |
| `ontology_chatbi/node/schema_retriever.py` | 209 |
| `ontology_chatbi/state.py` | 160 |
| `ontology_chatbi/constants.py` | 165 |
| `ontology_chatbi/node/tool_executor.py` | 151 |
| `ontology_chatbi/node/analysis_organizer.py` | 95 |
| `ontology_chatbi/node/context_compressor.py` | 41 |
| `ontology_chatbi/node/glossary_matcher.py` | 17 |
| `ontology_chatbi/node/skill_router.py` | 30 |
| `ontology_chatbi/views.py` | 44 |
| `ontology/data_query.py` | 1,383 |
| `ontology/extract_ontology.py` | 990 |
| `ontology/schema_optimizer.py` | 1,195 |
| `ontology/ontology_asset_validator.py` | 353 |
| `ontology/ontology_engine.py` | 298 |
| `ontology/schema_context.py` | 254 |
| `harness/harness_sql.py` | 521 |
| **总计** | **10,575** |
