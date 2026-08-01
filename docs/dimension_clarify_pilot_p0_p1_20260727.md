# 时间粒度维度组澄清 Agent：P0/P1 试点结果

> **日期**：2026-07-27  
> **范围**：以时间粒度维度组和少量高频 Metric 的通用绑定能力验证 P0/P1。  
> **不含**：前端选择组件、用户答案恢复 API、归因/根因分析、Concept 下钻。

## 1. 本次交付

| 项目 | 结果 |
|---|---|
| 新 Agent | 已新增确定性 `DimensionClarifyAgent` |
| 运行时维度资产 | `OntologyEngine` 已加载已批准的维度组、选项、映射和 Metric 绑定 |
| 查询链路接入 | 已在 Query Plan 校验后、工具执行前执行维度决策 |
| 未决维度处理 | 已启用 `State.CLARIFY` 并输出结构化 `clarification` 事件 |
| 缓存失效 | 已新增维度治理表变更触发本体版本更新的迁移 |
| 自动化验证 | 新增 6 个时间粒度澄清单元测试，全部通过 |

## 2. 实现位置

- 新 Agent：[backend/app/agents/clarify/engine.py](../../app/agents/clarify/engine.py)
- Agent 导出：[backend/app/agents/clarify/__init__.py](../../app/agents/clarify/__init__.py)
- 查询状态机接入：[backend/app/agents/data_query/engine.py](../../app/agents/data_query/engine.py)
- 运行时状态：[backend/app/agents/data_query/state.py](../../app/agents/data_query/state.py)
- 本体加载与维度组查询：[backend/app/core/ontology/ontology_engine.py](../../app/core/ontology/ontology_engine.py)
- 缓存版本迁移：[backend/alembic/versions/20260727_0018_add_dimension_groups_ontology_version_triggers.py](../../alembic/versions/20260727_0018_add_dimension_groups_ontology_version_triggers.py)
- 测试：[backend/tests/test_dimension_clarify_agent.py](../../tests/test_dimension_clarify_agent.py)

## 3. P0：维度资产进入运行时

`OntologyEngine.from_database()` 现在读取以下已批准资产：

1. `dimension_groups`；
2. `dimension_group_options`；
3. `dimension_field_mappings`；
4. `metric_dimension_bindings`。

运行时只保留可执行资产：组和选项必须为 `approved`，字段映射必须指向有效的本体 Class/字段，映射必须关联现存选项。加载后为 Metric 补充 `dimension_group_ids`，并提供：

- `get_dimension_group(group_id)`；
- `list_dimension_groups()`；
- `list_dimension_groups_for_metrics(metric_ids)`。

新增的 Alembic 迁移会在维度组、选项、字段映射和 Metric 绑定发生变更时调用既有 `bump_ontology_version()`，避免本体缓存继续使用旧的澄清配置。

## 4. P1：确定性时间粒度决策

`DimensionClarifyAgent` 不调用 LLM。对每个绑定在当前 Metric 上且标记为必选的维度组，按下面的优先级决策：

1. 已在 `dimensions` 或 `filters` 中出现的映射字段；
2. 已确认的会话答案（当前接口预留，P2 接入）；
3. 组选项的 `aliases` 命中用户表达；
4. 当策略允许时，使用组的 `default_option`；
5. 仍不可安全决定时，返回业务级澄清问题。

对于时间粒度，业务配置可以将“按月”“本月”“上月”配置为 AP 月选项别名，将“按季度”“本季度”配置为季度选项别名。Agent 仅把已选粒度转换为 `dimensions` 中的逻辑字段，例如：

| 选择 | 结果字段 | 行为 |
|---|---|---|
| `ap_month` | `apmonth` | 增加到分组维度 |
| `quarter` | `quarter_cd` | 增加到分组维度 |

它不根据“按季度”自行伪造具体季度过滤值；“统计粒度”和“时间范围”仍是两个独立的业务决策。

### 冲突保护

以下情况不会用默认值覆盖用户意图：

- 用户表达同时命中多个选项，例如“按月或按季度”；
- 选项、映射或组未批准；
- 所选项在当前 `target_class + join_classes` 范围没有可执行字段；
- 计划字段对应多个选项而无法反向唯一判断。

这类情况进入 `State.CLARIFY`，执行前不会访问数据源。

## 5. 状态机行为

```mermaid
flowchart LR
    A[Query Plan 基础校验通过] --> B[DimensionClarifyAgent.resolve]
    B -->|字段/语义/默认已确定| C[写入字段级 dimensions]
    C --> D[既有参数预处理与工具执行]
    B -->|必选时间粒度未决| E[State.CLARIFY]
    E --> F[clarification 结构化事件]
    F --> G[本轮结束，不执行查询]
```

事件仅包含业务名、稳定的组选项 ID 和展示标签，不包含物理字段：

```json
{
  "type": "clarification",
  "data": {
    "version": 1,
    "reason": "missing_dimension_groups",
    "questions": [
      {
        "group_id": "time_granularity",
        "group_name": "时间粒度",
        "options": [
          {"value": "ap_month", "label": "按 AP 月", "is_default": true},
          {"value": "quarter", "label": "按季度", "is_default": false}
        ]
      }
    ]
  }
}
```

## 6. 验证结果

已执行：

```text
python -m pytest backend/tests/test_dimension_clarify_agent.py -q --tb=short
```

结果：`6 passed`。

覆盖场景：

1. Query Plan 已显式包含 `quarter_cd` 时，识别为季度选项；
2. “本季度”通过选项别名解析为季度；
3. 无时间表达时，按允许策略应用 AP 月默认项；
4. “按月或按季度”命中多个选项时发起澄清，不使用默认项覆盖；
5. `always_ask` 时即使存在默认项也发起澄清；
6. Metric 绑定的维度组可由 `OntologyEngine` 读取。

另外已验证 `ChatEngineV3` 成功实例化并挂载新的 `DimensionClarifyAgent`。

## 7. 启用试点前的配置清单

对选定的高频 Metric，需要通过现有管理端完成以下配置：

1. 创建并审批 `time_granularity` 维度组；
2. 审批选项，例如 `ap_month`、`quarter`；
3. 维护别名，如“按月 / 本月 / 上月 / 按季度 / 本季度”；
4. 将每个选项映射到目标 Class 的逻辑字段，例如 `apmonth`、`quarter_cd`；
5. 将维度组绑定到试点 Metric；
6. 明确 `is_required`、`default_option` 与 `clarification_policy`；
7. 执行本次新增的数据库迁移，使本体缓存对配置变更失效。

本次代码不会硬编码具体 Metric ID，因此可先对 2–3 个业务确认的高频销售指标启用，避免把未经治理的默认口径写死在代码中。

## 8. 下一步（P2，不在本次实现范围）

1. 前端渲染 `clarification` 卡片；
2. 增加 `resume_token + clarification_answers` 恢复接口；
3. 校验答案后恢复原 Query Plan，避免再次调用 LLM 规划；
4. 在最终答案中披露自动选择，例如“已按默认 AP 月汇总”；
5. 增加跨 Metric 的 `allowed_options`、指标级默认项和冲突合并规则。
