结合你提供的两份设计文档和外部资料，我对这两个功能需求做了梳理，给出了具体的改进和实现方案。

---

### 一、澄清机制的改进：基于“分析维度组”的抽象

你提到的 `apmonth`、`quarter_cd`、`apyear` 这类字段级维度，直接暴露给用户和LLM，确实会带来认知割裂和维护困难的问题。解决的思路是在**指标（Metric）和物理字段（Physical Column）之间，引入一个“分析维度组（Dimension Group）”的抽象层**。

这个方案在业内被称为“语义层（Semantic Layer）”。它的核心价值在于：**将业务概念（如“时间粒度”）与底层的物理实现（如 `apmonth` 字段）解耦**。这样一来，指标只需声明它依赖哪个“维度组”，而无需关心具体是哪个字段。同时，这也让系统能生成业务友好的澄清问题，而不是让用户去选择物理字段。

#### 1.1 模型设计 (Pydantic)

首先，在 `schema_model.py` 中定义 `DimensionGroup` 及相关模型：

```python
from pydantic import BaseModel, Field
from typing import List, Optional

class DimensionOption(BaseModel):
    """维度组下的具体选项，如 '按AP月'"""
    value: str = Field(..., description="对应的物理列名，如 'apmonth'")
    label: str = Field(..., description="业务友好标签，如 '按AP月(如2026AP03)'")
    is_default: bool = Field(False, description="是否为默认选项")

class DimensionGroup(BaseModel):
    """分析维度组：指标与物理字段之间的桥梁"""
    id: str = Field(..., description="唯一标识，如 'time_granularity'")
    name: str = Field(..., description="业务名称，如 '时间粒度'")
    description: str = Field("", description="业务含义说明")
    group_type: str = Field("categorical", description="类型: categorical/time/hierarchy")
    options: List[DimensionOption] = Field(..., description="该维度组下的所有选项")
    is_required: bool = Field(False, description="是否为必选维度")
    default_option: Optional[str] = Field(None, description="默认选项的value")
```

然后，修改 `MetricOptimization` 模型，用新的字段替代旧的 `dimensions` 和 `required_dimensions`：

```python
class MetricOptimization(BaseModel):
    # ... 原有字段 ...
    # 移除: dimensions, required_dimensions
    analysis_dimension_groups: List[str] = Field(
        default_factory=list,
        description="关联的分析维度组ID列表，如 ['time_granularity', 'region']"
    )
```

#### 1.2 配置示例 (`schema.json`)

在 `schema.json` 中独立配置 `dimension_groups`，实现一次定义，多处复用：

```json
{
  "dimension_groups": [
    {
      "id": "time_granularity",
      "name": "时间粒度",
      "group_type": "time",
      "options": [
        {"value": "apmonth", "label": "按AP月查看", "is_default": true},
        {"value": "quarter_cd", "label": "按季度查看"},
        {"value": "apyear", "label": "按财年查看"}
      ],
      "is_required": true,
      "default_option": "apmonth"
    }
  ],
  "metrics": [
    {
      "id": "total_sales",
      "name": "销售总额",
      "analysis_dimension_groups": ["time_granularity"],
      "formula": "SUM(net_amount)"
    }
  ]
}
```

#### 1.3 澄清逻辑 (`_handle_clarify`)

当用户提问模糊，且必选维度组未被覆盖时，触发澄清流程：

1.  **校验**：在 `TOOL_EXECUTE` 前，校验 `query_ontology_data` 的参数是否覆盖了指标关联的所有 `is_required=True` 的维度组。
2.  **自动填充**：如果缺失的维度组有 `default_option`，或能从用户问题（如“最近”、“本月”）中解析出具体值，则自动填充，不打断用户。
3.  **生成澄清问题**：若无法自动填充，则构造一个业务友好的问题（如“您希望按哪种时间粒度查看？”），通过 SSE 事件发送给前端。

---

### 二、基于 Concept 的深度分析：设计 AnalysisAgent

你提到的 Concept 数据（`subject_domain` -> `dimension_group` -> `entity`）确实是进行深度追因分析的理想骨架。`AnalysisAgent` 的职责就是利用这个骨架，将“为什么”这类开放性问题，转化为一组结构化的、可执行的查询计划。

#### 2.1 核心工作流

1.  **意图识别**：在 `CONTEXT_PREP` 阶段，检测用户问题是否包含“为什么”、“下降”、“原因”等追因关键词。
2.  **目标定位**：识别用户关心的核心指标（Metric）。
3.  **归因拆解 (Attribution Decomposition)**：
    *   根据指标找到其所属的 `subject_domain`。
    *   从 Concept 树中，找出该 `subject_domain` 下的所有 `dimension_group`（如“区域”、“品类”）。
    *   为每个 `dimension_group` 生成一个对比查询计划（当前期 vs 对比期）。
4.  **并行执行**：使用 `ToolExecutor` 并行执行所有子查询，获取各维度下的数据。
5.  **贡献度计算与报告生成**：计算每个维度下各细项（如“华东区”）对总体变化的贡献度，并生成结构化的归因报告。

#### 2.2 贡献度计算

对于某个维度下的细项 `i`，其**波动贡献率**为：

$$C_i = \frac{V_{i, current} - V_{i, previous}}{\sum_{j} (V_{j, current} - V_{j, previous})} \times 100\%$$

其中，分母是所有细项变化值的总和。这个公式可以清晰地量化每个因素对总体变化的影响程度。

#### 2.3 状态机集成

在 `engine.py` 的状态机中新增 `ANALYSIS_PLAN` 状态，并修改路由逻辑：

```python
class State(StrEnum):
    # ... 原有状态 ...
    ANALYSIS_PLAN = "analysis_plan"  # 新增

# 在 _handle_context_prep 中
async def _handle_context_prep(self, state: AgentState) -> State:
    # ... 原有逻辑 ...
    if self._is_attribution_question(state.user_message):
        return State.ANALYSIS_PLAN
    # ... 原有路由 ...

# 新增状态处理器
async def _handle_analysis_plan(self, state: AgentState) -> State:
    # 1. 实例化 AnalysisAgent
    # 2. 调用 analysis_agent.plan_attribution_analysis() 生成计划
    # 3. 并行执行所有子查询
    # 4. 调用 analysis_agent.summarize_attribution() 生成报告
    # 5. 将报告注入 state，进入 FINAL_STREAM
    return State.FINAL_STREAM
```

---

### 三、总结与实施建议

| 改进点 | 核心思路 | 主要收益 |
| :--- | :--- | :--- |
| **维度澄清** | 引入“分析维度组”抽象层，解耦指标与物理字段。 | 降低维护成本，生成业务友好的澄清问题。 |
| **深度分析** | 引入 `AnalysisAgent`，利用 Concept 树驱动归因分析。 | 将“为什么”问题转化为结构化的、可解释的归因报告。 |

#### 实施路线图

1.  **Phase 1: 维度组落地 (P0)**
    *   在 `schema.json` 中定义 `dimension_groups`。
    *   更新 Pydantic 模型，修改现有 Metric 配置。
    *   实现 `_handle_clarify` 中的校验、自动填充和澄清逻辑。

2.  **Phase 2: AnalysisAgent 实现 (P1)**
    *   新建 `agents/analysis_agent.py`，实现核心的归因计划生成和报告汇总逻辑。
    *   在状态机中集成 `ANALYSIS_PLAN` 状态。

3.  **Phase 3: 前端与闭环优化 (P2)**
    *   适配前端的澄清问题 UI 和归因报告展示。
    *   建立反馈闭环，将用户纠错转化为术语库或规则的更新。