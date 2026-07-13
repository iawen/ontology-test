ChatBI 维度抽象澄清与 Concept 深度追因分析技术方案报告本报告

针对当前 ChatBI 系统的两个核心瓶颈——指标维度的细碎物理纠缠（澄清不友好）和多维 Concept 概念树资产闲置（无法进行追因分析），提供了确定性的技术改进方案、算法设计和架构演进路线。

## 一、 分析维度组（DimensionGroup）的设计与业务级澄清机制

### 1.1 核心痛点与改进思路
在目前的实现中，指标（Metric）的 required_dimensions（必选维度）和 dimensions（可选维度）直接绑定到物理字段名（如 apmonth, quarter_cd, apyear）。这带来两个主要弊端：
+ 认知割裂：LLM 必须直接理解底层的物理时间表达。当用户提问模糊时，系统生成的澄清问题（如 “请选择按 apmonth 还是 quarter_cd 过滤”）包含过多的物理库表细节，业务体验极差。
+ 高耦合：任何底层表字段的调整，都会导致大量的指标定义（Metric）同步重写。

改进方案：引入 分析维度组（DimensionGroup） 逻辑抽象层，作为 指标（Metric） 与 物理列名（Physical Columns） 之间的转换纽带。
```
【指标层 (Metric)】        ──> Total Sales (销售总额) 依赖 [时间粒度 (time_granularity)] 维度组
                                  │
【维度组层 (Group)】       ──> time_granularity (时间粒度) 
                                  ├── 业务标签: "按月(AP月)", "按季度", "按财年"
                                  └── 物理映射: SaleOrder.apmonth, SaleOrder.quarter_cd, SaleOrder.apyear
                                  │
【物理执行层 (Engine)】    ──> 最终翻译为 SQL 对应物理列的分组或过滤
```

1.2 Pydantic 模型扩展设计 (schema_model.py)
我们需要在 schema_model.py 中引入 DimensionGroup 及其支撑子模型，解耦物理字段。
```py
from pydantic import BaseModel, Field
from typing import List, Optional

class FieldMapping(BaseModel):
    """物理字段映射"""
    class_id: str = Field(..., description="绑定的逻辑类 ID")
    field_name: str = Field(..., description="对应的物理列名")
    display_name: str = Field(..., description="字段逻辑展示名，如 'AP月'")

class DimensionOption(BaseModel):
    """供澄清选择的业务级颗粒度选项"""
    value: str = Field(..., description="对应的物理列，如 apmonth")
    label: str = Field(..., description="对业务人员友好的标签，如 '按AP月(如2026AP03)'")
    is_default: bool = Field(False, description="是否为首选/默认聚合项")

class DimensionGroup(BaseModel):
    """分析维度组定义"""
    id: str = Field(..., description="维度组唯一标识，如 time_granularity, region")
    name: str = Field(..., description="维度组中文名称，如 '时间粒度'")
    description: str = Field("", description="业务含义")
    group_type: str = Field("categorical", description="类型: categorical/time/hierarchy")
    field_mappings: List[FieldMapping] = Field(default_factory=list, description="多表对齐映射")
    options: List[DimensionOption] = Field(default_factory=list, description="澄清决策选项")
    is_required: bool = Field(False, description="缺失该维度组时，是否强制挂起并触发主动澄清")
    default_option: Optional[str] = Field(None, description="默认选项，避免无谓反问")
```

同时，MetricOptimization 需要进行重构：
+ 废弃 required_dimensions: List[str] 和 dimensions: List[str] 的细粒度配置。
+ 新增 analysis_dimension_groups: List[str]，声明该指标关联的维度组 ID 集合。

### 1.3 澄清决策与触发逻辑 (_handle_clarify)
在状态机的 LLM_CALL 阶段，一旦 LLM 生成了 query_ontology_data 调用：
+ 校验器（Validator）拦截该调用的参数。
+ 扫描选定 Metric 的 analysis_dimension_groups 属性。
+ 检查当前 query_plan 的 dimensions 和 filters 是否包含该维度组下的任一物理映射字段。
+ 如果未包含，且该维度组 is_required 为 True，则中止执行，将状态导向 State.CLARIFY，并向前端 yield 结构化澄清事件。

确定性校验核心伪代码
```py
def check_missing_dimension_groups(query_args: dict, engine) -> List[dict]:
    metrics = query_args.get("metrics", [])
    provided_dimensions = set(query_args.get("dimensions", []))
    for f in query_args.get("filters", []):
        provided_dimensions.add(f.get("field"))

    missing_groups = []
    for m in metrics:
        metric_info = engine.get_metric_info(m)
        if not metric_info:
            continue
        
        # 获取关联的维度组
        group_ids = metric_info.get("analysis_dimension_groups", [])
        for gid in group_ids:
            group_meta = engine.get_dimension_group(gid) # 从引擎获取维度组元数据
            if not group_meta:
                continue
                
            # 提取该组下的所有物理列候选
            group_physicals = {opt.value for opt in group_meta.options}
            
            # 如果用户当前生成的参数未覆盖该维度组的任何物理字段，且该组是必选的
            if group_meta.is_required and not (provided_dimensions & group_physicals):
                missing_groups.append({
                    "group_id": group_meta.id,
                    "group_name": group_meta.name,
                    "options": [opt.dict() for opt in group_meta.options],
                    "default_option": group_meta.default_option
                })
    return missing_groups
```

### 1.4 智能自动填充策略（避免过度澄清）
为防止系统变成“查户口”式的繁琐对话，我们需要前置自然语言时间与空间解析器，实行自动填充（Auto-fill）防线：
+ 时间关键词自动识别：
  + 用户提问含“最近”、“上月”、“同比”、“环比”等词，自动将 time_granularity 维度组映射为 apmonth 并自动推断其值（如上个有数月份），无缝进入执行状态，不触发澄清。
+ 默认指标级填充：
  + 若用户完全未提供时间或分类暗示，主控直接采用维度组的 default_option（例如按 AP 月）填充，并以自然语言在回答中主动声明（例如：“已默认按 AP月 为您展示：...”），跳过澄清。

## 二、 基于 Concept 概念层级树的深度追因（AnalysisAgent）
### 2.1 核心痛点与因果推导逻辑
当用户提问：“为什么销售额下降了？”
+ 目前的窘境：系统只能调用 query_ontology_data 查出总额，然后依赖大模型强写 Python 代码去蒙蔽式比较，由于没有业务层级链条支持，难以实现深度的因果溯源（Root-Cause Analysis）。
+ 改进方案：引入一个无状态的 AnalysisAgent 算子。它不负责自由聊天，只负责利用本体中原本闲置的 concepts 层级，进行自动化归因计划拆解（RCA Attribution Drilling）。

### 2.2 Concept 树驱动的归因拆解算法设计
Concept 树定义了实体与指标的逻辑分类和业务主域（Subject Domain）。当追因意图触发时，AnalysisAgent 按照以下链路拆解分析任务： 
```
                             [ 用户问: 为什么6月销售额下降了 ]
                                              │
                                              ▼
                             [ 定位指标: total_sales 销售总额 ]
                                              │
                                              ▼
                         [ 沿概念树向上寻找关联的 subject_domain ]
                                  └── 销售主题域 (sales_domain)
                                              │
                                              ▼
                      [ 提取该 domain 下的所有子 dimension_group 概念 ]
                                  ├── 区域概念组 (region_dimension_group)
                                  ├── 品类概念组 (category_dimension_group)
                                  └── 渠道概念组 (channel_dimension_group)
                                              │
                                              ▼
                    [ 生成 N 路并行的子查询计划 (对比当前期 vs 环比/同比期) ]
```

### 2.3 核心算子设计与伪代码实现
#### 1. RCA 分析计划生成 (plan_attribution_analysis)
```py
class AnalysisAgent:
    def __init__(self, client, model_name: str):
        self.client = client
        self.model_name = model_name

    async def plan_attribution_analysis(
        self,
        user_message: str,
        target_metric_id: str,
        concept_tree: List[dict],
        engine
    ) -> dict:
        metric_info = engine.get_metric_info(target_metric_id)
        target_class = metric_info.get("target_class")
        
        # 1. 沿树向上追溯祖先节点，寻找 Level = 1 的主题域
        subject_domain_id = self._find_ancestor_domain(target_class, concept_tree)
        
        # 2. 获取该主题域下的所有维度组
        dimension_groups = [
            node for node in concept_tree 
            if node.get("parent_id") == subject_domain_id 
            and node.get("concept_type") == "dimension_group"
        ]
        
        # 3. 解析分析周期时间窗口 (当前期 vs 对比期)
        time_windows = self._parse_time_windows(user_message)
        
        # 4. 为每个维度组，自动生成一个对比下钻的物理子查询
        sub_queries = []
        for d_group in dimension_groups:
            related_cls = d_group.get("related_class")
            # 从 Class 中提取最合适的分组特征维度字段（逻辑字段）
            drilling_dimensions = self._get_drilling_dimensions(related_cls, engine)
            
            sub_queries.append({
                "dimension_group_id": d_group["id"],
                "dimension_group_name": d_group["name"],
                "target_class": target_class,
                "metrics": [target_metric_id],
                "dimensions": drilling_dimensions,
                # 过滤出当前期与对比期，供后续计算差值
                "filters": [
                    {
                        "field": "business_date",
                        "operator": "BETWEEN",
                        "value": [time_windows["comparison_start"], time_windows["current_end"]]
                    }
                ],
                "purpose": f"按 {d_group['name']} 进行多维异动贡献度下钻"
            })
            
        return {
            "analysis_type": "attribution",
            "target_metric_id": target_metric_id,
            "target_metric_name": metric_info.get("name_cn"),
            "time_windows": time_windows,
            "sub_queries": sub_queries
        }
```

#### 2. 指标贡献度量化计算模型 (summarize_attribution)
子查询并行执行完毕后，AnalysisAgent 需要利用数学公式对数据进行波动因果排序。计算某个维度细项（如 “华东区”）对总波动值的波动贡献率 $C_i$：
$$
C_i = \frac{V_{i, \text{current}} - V_{i, \text{previous}}}{\sum (V_{j, \text{current}} - V_{j, \text{previous}})} \times 100\%
$$
其中：
+ $V_{i, \text{current}}$ 是该细项在当前分析周期的值（如 2026AP06）。
+ $V_{i, \text{previous}}$ 是该细项在对比周期的值（如 2026AP05）。

```py
async def summarize_attribution(self, plan: dict, query_results: List[dict]) -> dict:
        current_period = plan["time_windows"]["current"]
        previous_period = plan["time_windows"]["previous"]
        
        dimensions_analysis = []
        for sub_q, result in zip(plan["sub_queries"], query_results):
            rows = result.get("rows", [])
            dim_group_name = sub_q["dimension_group_name"]
            
            # 计算每个物理维度值的变化量
            # 格式：{ "华东区": { "current": 100, "previous": 120, "diff": -20 } }
            changes = self._calculate_row_diffs(rows, current_period, previous_period)
            
            total_negative_diff = sum(c["diff"] for c in changes.values() if c["diff"] < 0)
            
            contributors = []
            for name, val in changes.items():
                # 计算其对降幅/涨幅的贡献度
                contribution_pct = (val["diff"] / total_negative_diff * 100) if total_negative_diff != 0 else 0
                contributors.append({
                    "dim_value": name,
                    "diff_value": val["diff"],
                    "contribution_ratio": round(contribution_pct, 2)
                })
                
            # 按贡献绝对值从大到小排序
            contributors.sort(key=lambda x: abs(x["diff_value"]), reverse=True)
            
            dimensions_analysis.append({
                "dimension_group_name": dim_group_name,
                "top_contributor": contributors[0] if contributors else None,
                "details": contributors
            })
            
        # 根据量化数据，生成确定性的 RCA 分析报告
        summary_text = self._build_natural_rca_report(plan["target_metric_name"], dimensions_analysis)
        return {
            "summary": summary_text,
            "structured_data": dimensions_analysis
        }
```

## 三、 主状态机（FSM）集成与执行生命周期
为了承载上述两个全新功能，主控状态机（engine.py）需要进行平滑流转演进。
### 3.1 演进后的 9 状态生命周期流转图
```
   INIT (初始化)
     │
     ▼
 CONTEXT_PREP (准备上下文 & 意图拦截) ──(检测到为什么/下降/异常)──>  ANALYSIS_PLAN (并行多路归因)
     │                                                               │
     ▼                                                               ▼
  LLM_CALL (模型推理 & 缺失维度拦截)                                 FINAL_STREAM (输出归因报告)
     │                                                               │
     ├───(缺失必选维度组)──> State.CLARIFY (业务主动澄清)              │
     │                                                               │
     ├───(正常工具调用)───> State.TOOL_EXECUTE (安全参数化执行)        │
     │                                                               │
     ▼                                                               ▼
   DONE <────────────────────────────────────────────────────────────┘
```

### 3.2 SSE (Server-Sent Events) 前端交互契约定义
在 State.CLARIFY（主动澄清状态）和归因分析状态中，SSE 向前端派发高可读性的结构化 JSON，保持用户体验的专业性。

#### 1. 业务级澄清 SSE 契约
前端收到后直接渲染为单选/多选按钮，无需渲染物理字段。
```json
{
  "type": "clarification",
  "message": "为了提供更精准的数据，我需要与您确认以下分析维度：",
  "questions": [
    {
      "group_id": "time_granularity",
      "group_name": "时间粒度",
      "options": [
        {"value": "apmonth", "label": "按AP月查看（推荐）", "is_default": true},
        {"value": "quarter_cd", "label": "按季度查看"},
        {"value": "apyear", "label": "按财年查看"}
      ]
    }
  ]
}
```
#### 2. 因果追因 RCA 状态 SSE
契约由于归因分析可能会执行 3~4 个子查询，通过流式输出，让用户清晰看到分析步骤。
```json
{
  "type": "tool",
  "name": "query_ontology_data",
  "description": "下钻：基于 Concept「品类」维度深度透视该指标的变化贡献度"
}
```

## 四、 实施路线图 (Roadmap)
我们建议按照以下三个阶段，渐进式地在你的工程代码中落地本方案：
### 📈 第一阶段：DimensionGroup 物理对准与澄清拦截 (P0)
+ 更新 Pydantic 实体：在 schema_model.py 中增加 DimensionGroup 等数据模型，支持其字段解析。
+ 在 schema.json 中配置：单独开辟 "dimension_groups" 配置，将时间字段归入 "time_granularity"。
+ 改动 engine.py：
  + 将 entity_agent.disambiguate 移入 ToolExecutor 内部，彻底消灭冷启动延迟。
  + 在 LLM_CALL 阶段，增加对 query_ontology_data 中指标维度组是否缺失的自动校验拦截，触发 State.CLARIFY。

### 📈 第二阶段：AnalysisAgent 概念归因计算落地 (P1)
+ 新建 Agent：在 agents.py 中新增 AnalysisAgent，编写祖先主题域追溯算法和基于贡献度公式的计算函数。
+ 状态机扩展：在 engine.py 中定义新状态 ANALYSIS_PLAN。在 CONTEXT_PREP 阶段拦截“为什么/下降”等意图，绕过大模型写 Python 脚本的黑盒行为，走向确定性高的归因管道。

###  📈 第三阶段：前端图表升级与闭环 (P2)
+ 契约对齐：规范 python_analyze 与归因分析产生的结果数据集，确保前端图表能直接取用聚合数据。
+ 历史反哺：用户纠错反馈可自动转换并注册入 glossary (术语库)，实现线上的自动演进。