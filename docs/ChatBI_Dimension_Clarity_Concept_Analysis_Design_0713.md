# ChatBI 维度抽象澄清与 Concept 深度追因分析方案

> **日期**: 2026-07-13
> **背景**: 基于当前 Chat V3 Plan-Execute 架构，解决两个核心问题：
> 1. `_handle_clarify` 的澄清维度过于细碎（字段级），需要基于 Metric 进行业务级抽象
> 2. Concept 数据已有但未利用，需要设计独立的 AnalysisAgent 进行深度追因分析

---

## 一、问题 1：基于 Metric 的维度抽象与澄清策略

### 1.1 现状问题

当前 `required_dimensions` 和 `dimensions` 都是字段级别的（如 `apmonth`、`quarter_cd`、`apyear`），导致：
- **用户难以理解**：用户不知道"apmonth"是什么，只关心"按月"还是"按季度"
- **Metric 维护复杂**：每个 Metric 都要硬编码字段名，一旦底层表结构变化就要改 Metric
- **澄清问题不自然**：系统问"请选择 apmonth 还是 quarter_cd？"，而非"您希望按月、季度还是财年查看？"

### 1.2 解决方案：引入"分析维度组"抽象层

**核心思路**：在 Metric 和物理字段之间引入一层"分析维度组"（Analysis Dimension Group），将物理字段映射为业务可理解的维度概念。

#### 1.2.1 数据模型扩展

在 `schema_model.py` 中新增 `DimensionGroup` 模型：

```python
class DimensionGroup(BaseModel):
    """分析维度组：将物理字段抽象为业务可理解的维度概念"""
    id: str = Field(..., description="维度组ID，如 time_granularity、region、category")
    name: str = Field(..., description="业务名称，如 '时间粒度'、'区域'、'品类'")
    description: str = Field("", description="维度组说明")
    group_type: str = Field("categorical", description="类型: categorical/time/hierarchy")
    
    # 物理字段映射（按优先级排序，系统按顺序尝试）
    field_mappings: List[dict] = Field(
        default_factory=list,
        description="物理字段映射列表，每项含 class_id, field_name, display_name"
    )
    
    # 可选项（用于澄清问题生成）
    options: List[dict] = Field(
        default_factory=list,
        description="可选值列表，每项含 value(物理字段名), label(业务名称), is_default"
    )
    
    # 是否必选（用于触发澄清）
    is_required: bool = Field(False, description="是否为必选维度（缺失时触发澄清）")
    
    # 默认值（用于自动填充，避免每次都追问）
    default_option: Optional[str] = Field(None, description="默认选项的 value")
```

#### 1.2.2 Metric 模型调整

在 `MetricOptimization` 中增加 `analysis_dimension_groups` 字段，替代 `required_dimensions`：

```python
class MetricOptimization(BaseModel):
    # ... 原有字段 ...
    
    # 废弃：required_dimensions: List[str] = Field(default_factory=list)
    
    # 新增：分析维度组引用
    analysis_dimension_groups: List[str] = Field(
        default_factory=list,
        description="关联的分析维度组ID列表，如 ['time_granularity', 'region']"
    )
    
    # 新增：默认时间粒度（用于自动填充）
    default_time_granularity: Optional[str] = Field(
        None,
        description="默认时间粒度选项值，如 'apmonth'、'quarter_cd'"
    )
```

#### 1.2.3 配置示例

**时间粒度维度组**（全局共享，定义一次）：

```json
{
  "id": "time_granularity",
  "name": "时间粒度",
  "description": "数据按什么时间周期聚合",
  "group_type": "time",
  "field_mappings": [
    {"class_id": "SaleOrder", "field_name": "apmonth", "display_name": "AP月"},
    {"class_id": "SaleOrder", "field_name": "quarter_cd", "display_name": "季度"},
    {"class_id": "SaleOrder", "field_name": "apyear", "display_name": "财年"}
  ],
  "options": [
    {"value": "apmonth", "label": "按AP月（如2026AP03）", "is_default": true},
    {"value": "quarter_cd", "label": "按季度（如2026Q1）", "is_default": false},
    {"value": "apyear", "label": "按财年（如2026）", "is_default": false}
  ],
  "is_required": true,
  "default_option": "apmonth"
}
```

**Metric 关联维度组**：

```json
{
  "id": "total_sales",
  "name": "销售总额",
  "analysis_dimension_groups": ["time_granularity", "region", "category"],
  "default_time_granularity": "apmonth"
}
```

### 1.3 澄清流程改造

#### 1.3.1 触发条件

在 `_handle_query_plan` 中，当 LLM 生成的查询参数缺少 `analysis_dimension_groups` 中标记为 `is_required: true` 的维度时，触发 `CLARIFY` 状态。

```python
async def _handle_query_plan(self, state: AgentState) -> State:
    # ... 原有规划逻辑 ...
    
    if validation["valid"]:
        # 新增：校验分析维度组
        missing_groups = self._check_required_dimension_groups(
            validation["query_plan"], 
            state.metric_candidates,
            engine
        )
        if missing_groups:
            state.clarification_request = {
                "type": "missing_dimension_groups",
                "missing_groups": missing_groups,
                "user_message": state.user_message,
            }
            return State.CLARIFY
    
    # ... 继续执行 ...
```

#### 1.3.2 `_handle_clarify` 实现

```python
async def _handle_clarify(self, state: AgentState) -> State:
    """基于分析维度组生成业务级澄清问题"""
    clarification = state.clarification_request
    if not clarification:
        return State.DONE
    
    missing_groups = clarification.get("missing_groups", [])
    if not missing_groups:
        return State.DONE
    
    # 构建澄清问题
    questions = []
    for group in missing_groups:
        options = group.get("options", [])
        default = group.get("default_option")
        
        # 如果有默认值，自动填充而非追问
        if default and self._should_auto_fill(group, state.user_message):
            questions.append({
                "group_id": group["id"],
                "auto_filled": True,
                "selected_value": default,
                "label": group["name"],
            })
            continue
        
        # 生成自然语言澄清问题
        option_labels = [opt["label"] for opt in options]
        question_text = f"关于「{group['name']}」，您希望按哪种方式查看？"
        
        questions.append({
            "group_id": group["id"],
            "auto_filled": False,
            "question": question_text,
            "options": options,
            "label": group["name"],
        })
    
    # 如果所有缺失维度都有默认值，自动填充后继续执行
    if all(q.get("auto_filled") for q in questions):
        state.query_plan = self._apply_auto_filled_dimensions(
            state.query_plan, questions
        )
        return State.TOOL_EXECUTE
    
    # 发送澄清事件给前端
    state.sse_events.append({
        "type": "clarification",
        "questions": questions,
        "user_message": state.user_message,
    })
    
    return State.DONE  # 等待用户回答
```

#### 1.3.3 澄清问题示例

**用户问**："销售额是多少？"

**系统检测**：Metric `total_sales` 关联了 `time_granularity`（必选），但用户未指定。

**生成的澄清问题**（SSE 事件）：

```json
{
  "type": "clarification",
  "questions": [
    {
      "group_id": "time_granularity",
      "question": "关于「时间粒度」，您希望按哪种方式查看？",
      "options": [
        {"value": "apmonth", "label": "按AP月（如2026AP03）", "is_default": true},
        {"value": "quarter_cd", "label": "按季度（如2026Q1）"},
        {"value": "apyear", "label": "按财年（如2026）"}
      ],
      "label": "时间粒度"
    }
  ]
}
```

**前端展示**：

> 🤔 关于「时间粒度」，您希望按哪种方式查看？
> - ○ 按 AP 月（如 2026AP03）✅ 推荐
> - ○ 按季度（如 2026Q1）
> - ○ 按财年（如 2026）

### 1.4 自动填充策略

为避免每次都追问，系统应优先尝试自动填充：

| 场景 | 策略 | 示例 |
|------|------|------|
| 用户说"最近" | 自动填充为默认 AP 月 + 最近一个有数据的月份 | `apmonth = 2026AP06` |
| 用户说"本季度" | 自动填充为 `quarter_cd = 2026Q2` | 无需追问 |
| 用户说"今年" | 自动填充为 `apyear = 2026` | 无需追问 |
| 用户完全未提时间 | 使用 Metric 的 `default_time_granularity` | 默认按 AP 月 |
| 用户说"按区域" | 自动识别 `region` 维度组，不追问 | 无需追问 |

```python
def _should_auto_fill(self, group: dict, user_message: str) -> bool:
    """判断是否应该自动填充而非追问"""
    if group["group_type"] == "time":
        # 时间维度组：如果用户消息中包含时间关键词，尝试自动解析
        time_keywords = ["最近", "本月", "本季度", "今年", "上周", "上月"]
        if any(kw in user_message for kw in time_keywords):
            return True
        # 如果有默认值且用户未明确拒绝，使用默认值
        if group.get("default_option"):
            return True
    elif group["group_type"] == "categorical":
        # 分类维度组：如果用户消息中包含明确的分类值，自动匹配
        return False  # 需要用户明确指定
    return False
```

### 1.5 优势总结

| 维度 | 改进前 | 改进后 |
|------|--------|--------|
| **用户理解** | "请选择 apmonth" | "您希望按 AP 月、季度还是财年查看？" |
| **Metric 维护** | 每个 Metric 硬编码字段名 | Metric 只引用维度组 ID，字段映射集中管理 |
| **扩展性** | 新增时间粒度需改所有 Metric | 只需在维度组中增加一个 option |
| **澄清体验** | 每次都追问 | 有默认值时自动填充，仅在必要时追问 |
| **多表支持** | 字段名必须一致 | 通过 `field_mappings` 支持不同表的不同字段名 |

---

## 二、问题 2：基于 Concept 的深度追因 AnalysisAgent

### 2.1 现状问题

当前 Concept 数据（`subject_domain → dimension_group/fact_group → entity/kpi`）已存在于 `schema.json` 中，但：
- **未参与查询规划**：`SchemaRetrieverAgent` 只检索 Class/Metric，不检索 Concept
- **未参与深度分析**：用户问"为什么销售额下降？"时，系统只查一个数据，不会自动拆解为多维度归因
- **未利用层级关系**：Concept 树的 `parent_id` 关系未被用于 drill-down 路径推导

### 2.2 解决方案：设计 AnalysisAgent

**核心思路**：利用 Concept 树的层级关系，自动生成"归因分析计划"，将"为什么"问题拆解为多个"是什么"子问题，分别查询后汇总归因。

#### 2.2.1 AnalysisAgent 职责定义

```python
class AnalysisAgent:
    """
    基于 Concept 层级的深度追因分析 Agent。
    
    职责：
    1. 识别用户问题中的"追因/归因"意图（如"为什么"、"原因"、"下降"、"增长"）
    2. 根据 Concept 树定位目标 Metric 所属的 subject_domain
    3. 从 subject_domain 下找出所有 dimension_group（如区域、品类、时间）
    4. 为每个 dimension_group 生成对比查询计划（当前 vs 对比期）
    5. 汇总各维度贡献度，生成归因报告
    
    契约：
      输入: user_message, metric_candidates, concept_tree, engine
      输出: {
          "analysis_type": "attribution" | "comparison" | "trend",
          "target_metric": "total_sales",
          "attribution_dimensions": ["region", "category", "time"],
          "sub_queries": [...],
          "summary": "销售额下降主要来自华东区(-23%)和坚果品类(-15%)"
      }
    """
```

#### 2.2.2 Concept 树驱动的归因拆解

**Concept 树结构示例**：

```
subject_domain: sales (销售主题域)
├── dimension_group: region (区域维度组)
│   └── related_class: Store
│       └── fields: province, city, region
├── dimension_group: category (品类维度组)
│   └── related_class: Product
│       └── fields: category, sub_category
├── dimension_group: time (时间维度组)
│   └── related_class: SaleOrder
│       └── fields: apmonth, quarter_cd
└── fact_group: sales_kpi (销售指标组)
    └── related_class: SaleOrder
        └── metrics: total_sales, avg_unit_price, order_count
```

**归因拆解逻辑**：

```python
async def plan_attribution_analysis(
    self,
    user_message: str,
    target_metric_id: str,
    concept_tree: list[dict],
    engine: OntologyEngine,
) -> dict:
    """
    基于 Concept 树生成归因分析计划。
    
    步骤：
    1. 找到 target_metric 所属的 subject_domain
    2. 从 subject_domain 下找出所有 dimension_group
    3. 为每个 dimension_group 生成对比查询（当前期 vs 对比期）
    4. 返回归因分析计划
    """
    # 1. 定位 Metric 的 subject_domain
    metric_info = engine.get_metric_info(target_metric_id)
    target_class = metric_info.get("target_class", "")
    
    # 2. 找到包含该 class 的 subject_domain
    subject_domain = self._find_subject_domain(target_class, concept_tree)
    
    # 3. 获取该 domain 下的所有 dimension_group
    dimension_groups = self._get_dimension_groups(subject_domain, concept_tree)
    
    # 4. 解析时间范围（当前期 vs 对比期）
    time_range = self._parse_time_range(user_message)
    # 如：当前期 = 2026AP06, 对比期 = 2026AP05
    
    # 5. 为每个 dimension_group 生成对比查询计划
    sub_queries = []
    for dim_group in dimension_groups:
        related_class = dim_group.get("related_class", "")
        dimensions = self._get_dimensions_from_class(related_class, engine)
        
        sub_queries.append({
            "dimension_group_id": dim_group["id"],
            "dimension_group_name": dim_group["name"],
            "target_class": target_class,
            "metrics": [target_metric_id],
            "dimensions": dimensions,
            "filters": [
                {"field": "apmonth", "operator": "IN", "value": [time_range["current"], time_range["comparison"]]}
            ],
            "purpose": f"按{dim_group['name']}拆解{metric_info['name']}变化",
        })
    
    return {
        "analysis_type": "attribution",
        "target_metric": target_metric_id,
        "target_metric_name": metric_info.get("name", ""),
        "time_range": time_range,
        "attribution_dimensions": [dg["name"] for dg in dimension_groups],
        "sub_queries": sub_queries,
    }
```

#### 2.2.3 归因分析执行流程

```
用户问："为什么 2026AP06 销售额比上月下降了？"
                    ↓
    ┌──────────────────────────────────┐
    │ AnalysisAgent.plan_attribution   │
    │                                  │
    │ 1. 识别 target_metric: total_sales│
    │ 2. 定位 subject_domain: sales    │
    │ 3. 获取 dimension_groups:        │
    │    - region (区域)               │
    │    - category (品类)             │
    │    - channel (渠道)              │
    │ 4. 解析时间: 当前=AP06, 对比=AP05│
    └──────────────┬──────────────────┘
                   ↓
    ┌──────────────────────────────────┐
    │ 生成 3 个子查询计划              │
    │                                  │
    │ Query 1: 按区域拆解              │
    │   SELECT province, apmonth,      │
    │   SUM(net_amount)                │
    │   WHERE apmonth IN (AP05, AP06)  │
    │   GROUP BY province, apmonth     │
    │                                  │
    │ Query 2: 按品类拆解              │
    │   SELECT category, apmonth,      │
    │   SUM(net_amount)                │
    │   WHERE apmonth IN (AP05, AP06)  │
    │   GROUP BY category, apmonth     │
    │                                  │
    │ Query 3: 按渠道拆解              │
    │   SELECT channel, apmonth,       │
    │   SUM(net_amount)                │
    │   WHERE apmonth IN (AP05, AP06)  │
    │   GROUP BY channel, apmonth      │
    └──────────────┬──────────────────┘
                   ↓
    ┌──────────────────────────────────┐
    │ 并行执行子查询（复用现有          │
    │ ToolExecutor + DataQueryEngine） │
    └──────────────┬──────────────────┘
                   ↓
    ┌──────────────────────────────────┐
    │ AnalysisAgent.summarize_attribution│
    │                                  │
    │ 计算各维度贡献度：               │
    │ - 华东区: -23% (贡献下降的45%)   │
    │ - 坚果品类: -15% (贡献下降的30%) │
    │ - 线上渠道: -8% (贡献下降的15%)  │
    │                                  │
    │ 生成归因报告：                   │
    │ "销售额下降主要来自华东区(-23%)   │
    │  和坚果品类(-15%)，建议关注..."  │
    └──────────────────────────────────┘
```

#### 2.2.4 归因报告生成

```python
async def summarize_attribution(
    self,
    analysis_plan: dict,
    query_results: list[dict],
) -> dict:
    """汇总各维度查询结果，计算贡献度，生成归因报告"""
    
    current_period = analysis_plan["time_range"]["current"]
    comparison_period = analysis_plan["time_range"]["comparison"]
    
    attribution_results = []
    
    for sub_query, result in zip(analysis_plan["sub_queries"], query_results):
        dim_name = sub_query["dimension_group_name"]
        rows = result.get("rows", [])
        
        # 计算每个维度值的环比变化
        dim_changes = self._calculate_dimension_changes(
            rows, current_period, comparison_period
        )
        
        # 找出贡献最大的 Top 3 维度值
        top_contributors = sorted(
            dim_changes, key=lambda x: abs(x["change_amount"]), reverse=True
        )[:3]
        
        attribution_results.append({
            "dimension": dim_name,
            "top_contributors": top_contributors,
            "total_change": sum(dc["change_amount"] for dc in dim_changes),
        })
    
    # 生成自然语言归因摘要
    summary = self._generate_attribution_summary(
        analysis_plan["target_metric_name"],
        attribution_results,
        current_period,
        comparison_period,
    )
    
    return {
        "analysis_type": "attribution",
        "target_metric": analysis_plan["target_metric"],
        "time_range": analysis_plan["time_range"],
        "attribution_results": attribution_results,
        "summary": summary,
    }
```

#### 2.2.5 归因报告示例

**用户问**："为什么 2026AP06 销售额比上月下降了？"

**系统返回**：

```json
{
  "analysis_type": "attribution",
  "target_metric": "total_sales",
  "time_range": {
    "current": "2026AP06",
    "comparison": "2026AP05"
  },
  "attribution_results": [
    {
      "dimension": "区域",
      "top_contributors": [
        {"value": "华东区", "current": 450000, "previous": 585000, "change_amount": -135000, "change_pct": -23.1},
        {"value": "华南区", "current": 380000, "previous": 395000, "change_amount": -15000, "change_pct": -3.8},
        {"value": "华北区", "current": 320000, "previous": 310000, "change_amount": 10000, "change_pct": 3.2}
      ],
      "total_change": -140000
    },
    {
      "dimension": "品类",
      "top_contributors": [
        {"value": "坚果", "current": 280000, "previous": 330000, "change_amount": -50000, "change_pct": -15.2},
        {"value": "炒货", "current": 420000, "previous": 440000, "change_amount": -20000, "change_pct": -4.5},
        {"value": "礼盒", "current": 450000, "previous": 520000, "change_amount": -70000, "change_pct": -13.5}
      ],
      "total_change": -140000
    }
  ],
  "summary": "2026AP06 销售额环比下降 14万元（-12.7%），主要原因：\n1. 区域方面：华东区下降 13.5万元（-23.1%），贡献了 96% 的降幅；\n2. 品类方面：礼盒下降 7万元（-13.5%），坚果下降 5万元（-15.2%）；\n建议重点关注华东区礼盒和坚果品类的销售情况。"
}
```

### 2.3 与现有架构的集成

#### 2.3.1 状态机扩展

在 `State` 枚举中新增 `ANALYSIS_PLAN` 状态：

```python
class State(StrEnum):
    # ... 原有状态 ...
    ANALYSIS_PLAN = "analysis_plan"  # 新增：归因分析规划
```

#### 2.3.2 触发条件

在 `_handle_context_prep` 中，当识别到"追因/归因"意图时，路由到 `ANALYSIS_PLAN`：

```python
async def _handle_context_prep(self, state: AgentState) -> State:
    # ... 原有逻辑 ...
    
    # 新增：检测归因分析意图
    if self._is_attribution_question(state.user_message):
        state.execution_mode = "attribution_analysis"
        return State.ANALYSIS_PLAN
    
    # ... 原有路由逻辑 ...

def _is_attribution_question(self, message: str) -> bool:
    """检测是否为归因分析类问题"""
    attribution_keywords = [
        "为什么", "原因", "归因", "导致", "下降", "增长",
        "变化", "驱动", "贡献", "影响", "why", "reason", "cause"
    ]
    return any(kw in message.lower() for kw in attribution_keywords)
```

#### 2.3.3 AnalysisAgent 实例化

在 `ChatEngineV3.__init__` 中新增：

```python
class ChatEngineV3:
    def __init__(self):
        # ... 原有 Agent ...
        self.analysis_agent = AnalysisAgent(self.client, self.model_name)
```

#### 2.3.4 状态处理函数

```python
async def _handle_analysis_plan(self, state: AgentState) -> State:
    """归因分析规划与执行"""
    engine = get_engine(state.agent_id)
    query_engine = get_query_engine(state.agent_id)
    executor = ToolExecutor(state.agent_id, self.entity_agent)
    
    # 1. 识别目标 Metric
    target_metric = self._identify_target_metric(state.user_message, state.metric_candidates)
    if not target_metric:
        state.error = "无法识别要分析的目标指标"
        return State.ERROR
    
    # 2. 获取 Concept 树
    concept_tree = engine.schema.get("concepts", [])
    
    # 3. 生成归因分析计划
    analysis_plan = await self.analysis_agent.plan_attribution_analysis(
        state.user_message,
        target_metric,
        concept_tree,
        engine,
    )
    
    # 4. 并行执行子查询
    query_results = []
    for sub_query in analysis_plan["sub_queries"]:
        result = await executor.execute("query_ontology_data", sub_query, query_engine, engine)
        query_results.append(result)
    
    # 5. 汇总归因报告
    attribution_report = await self.analysis_agent.summarize_attribution(
        analysis_plan, query_results
    )
    
    # 6. 注入到最终回答
    state.all_tool_results.append({
        "name": "attribution_analysis",
        "description": f"归因分析：{attribution_report['summary'][:100]}",
        "result": attribution_report,
    })
    
    state.final_reason = "attribution_analysis"
    state.assistant_content = attribution_report["summary"]
    return State.FINAL_STREAM
```

### 2.4 技术依据

| 依据来源 | 核心观点 | 应用场景 |
|---------|---------|---------|
| **CausalTrace (arXiv:2510.12033)** | 利用工业本体和知识图谱进行数据驱动的因果分析 | AnalysisAgent 的归因拆解逻辑 |
| **dbt Semantic Layer** | 时间维度需要粒度声明，形成自然层级，支持上下钻取 | DimensionGroup 的 `group_type: "time"` 设计 |
| **Tellius: Semantic Layers Evolving with Agentic AI** | 本体/知识图谱解析业务层级（如 Q3、Region），引导 drill-down | Concept 树驱动的归因维度发现 |
| **Microsoft: Exploring LLM-based Agents for RCA** | LLM Agent 通过 ReAct 模式进行根因分析 | AnalysisAgent 的"规划→执行→汇总"流程 |
| **AtScale: Rise of LLM Agents in Data Analytics** | 语义层提供受治理的指标和维度，Agent 在此基础上自主分析 | DimensionGroup 作为 Metric 和物理字段的中间抽象层 |

### 2.5 优势总结

| 维度 | 改进前 | 改进后 |
|------|--------|--------|
| **Concept 利用** | 数据存在但未使用 | 驱动归因维度发现，自动拆解分析 |
| **归因分析** | 只查一个数据，无拆解 | 自动按区域/品类/渠道等多维度拆解 |
| **用户体验** | "销售额下降了" → 数据表格 | "销售额下降了" → 归因报告+建议 |
| **扩展性** | 每种分析需硬编码 | Concept 树变化时自动适应新维度 |
| **可解释性** | 黑盒查询 | 每个维度贡献度量化呈现 |

---

## 三、实施路线图

### Phase 1：DimensionGroup 抽象层（1-2周）

1. 在 `schema_model.py` 中新增 `DimensionGroup` 模型
2. 在 `schema.json` 中增加 `dimension_groups` 配置段
3. 在 `OntologyEngine` 中增加 `get_dimension_groups()` 方法
4. 修改 `MetricOptimization`，用 `analysis_dimension_groups` 替代 `required_dimensions`
5. 实现 `_handle_clarify` 的业务级澄清逻辑

### Phase 2：AnalysisAgent 基础版（2-3周）

1. 新建 `node/analysis_agent.py`
2. 实现 `plan_attribution_analysis`（基于 Concept 树拆解）
3. 实现 `summarize_attribution`（贡献度计算+报告生成）
4. 在 `State` 枚举中新增 `ANALYSIS_PLAN`
5. 在 `_handle_context_prep` 中增加归因意图检测

### Phase 3：优化与迭代（1-2周）

1. 实现自动填充策略（时间关键词解析）
2. 支持多级 drill-down（如区域→省份→城市）
3. 增加趋势分析（trend）和对比分析（comparison）模式
4. 前端适配澄清问题 UI 和归因报告可视化

---

## 四、总结

| 问题 | 方案 | 核心创新 |
|------|------|---------|
| 澄清维度太细 | DimensionGroup 抽象层 | 在 Metric 和物理字段之间引入业务级维度概念，支持自动填充 |
| Concept 未利用 | AnalysisAgent | 基于 Concept 树自动发现归因维度，生成多维度对比查询计划 |

两个方案共同的设计哲学是：**让系统理解业务概念，而非物理字段**。DimensionGroup 让澄清问题从"选字段"升级为"选业务视角"，AnalysisAgent 让分析从"查数据"升级为"自动归因"。
