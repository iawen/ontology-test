# 窄表 KPI Metric 建模方案评估与推荐

**结论：**“在 Metric 增加表形态和 KPI 定位条件，并在 `data_query` 生成不同 SQL”的方向是可行的，能够解决当前 Pfizer `t_person_level_kpis_202606181333.csv` 的问题；但不建议将最终模型固化为仅有 `target_type` 与一个自由文本 `kpi where` 的专用分支。

推荐将其提升为**语义指标（semantic metric）= 聚合规则 + 值字段 + 受控的指标级筛选条件**。其中“宽表 / 窄表”只是指标来源的物理布局；`kpi_name = 'xxx'` 是指标定义内部的受控筛选条件。这样既能覆盖当前 KPI 名称列式窄表，也能自然扩展至渠道、地区、实际/目标、版本等多维度筛选，而不把 SQL 或判断逻辑交给 LLM。

---

## 1. 问题、数据事实与当前约束

### 1.1 当前 Pfizer 窄表的实际结构

已检查 `t_person_level_kpis_202606181333.csv`：

- 共 **22,558** 条记录；
- 共有 `apmonth`、人员层级/NT ID、`role`、`product_en_name`、`kpi_name`、`kpi_value`、规则与灯色等字段；
- 同一个 `kpi_name` 在不同人员、月份、角色、产品下重复出现。例如 `MTD A/T%`、`QTD A/T%` 各有 1,242 行；
- `kpi_value` 有空值，且 KPI 值的业务含义可能是金额、数量、比例、评分或计数；
- 注册的 `PersonLevelKpi` Class 已把 `KPI名称` 映射为 `kpi_name`、`KPI指标值` 映射为 `kpi_value`，但目前把 `kpi_name` 设为主键。

最后一点需要优先修正：`kpi_name` 在本表中显然不是唯一键。它最多是一个**指标标识维度**，不是事实行主键。错误主键会影响关系推断、去重和后续多指标查询的语义正确性。

### 1.2 当前系统的行为

当前 Metric 是“目标 Class + 自由公式”的元数据：

- 管理端类型只包含 `formula`、`calculation`、`filters_hint` 等字段；[src/admin/src/lib/types.ts](../src/admin/src/lib/types.ts)
- 后端 `MetricCreate` / `MetricUpdate` 与 `metrics` 表使用同一套自由公式模型；[src/backend/core/models/models.py](../src/backend/core/models/models.py) 和 [src/backend/core/db/db_provider.py](../src/backend/core/db/db_provider.py)；
- Metric 保存后会导出到场景 `schema.json`；导出采用白名单字段；[src/backend/modules/schema.py](../src/backend/modules/schema.py)；
- `DataQueryEngine` 对简单聚合公式进行字段映射，并将普通筛选编译到 `WHERE`、指标条件编译到 `HAVING`；[src/backend/core/ontology/data_query.py](../src/backend/core/ontology/data_query.py)；
- 查询规划器看到的是 Metric 名称、公式和描述，不应依靠模型在 `filters_hint` 中理解、拼接或执行 KPI 定位条件。

因此，仅改管理端或仅在数据库中新增字段都不够：新元数据必须经历 **CRUD → 场景导出/导入 → ontology context → 查询编译 → 测试** 的完整链路。

---

## 2. 对“`target_type` + `kpi where`”方案的评估

### 2.1 可行的部分

该方案抓住了两个本质：

1. 指标可来自不同物理布局：宽表直接聚合某一数值列，窄表需要先从值列中定位对应的指标成员；
2. 窄表中的 KPI 选择条件属于**指标定义自身**，而不是用户问题里的临时过滤条件。

以 `kpi_name = 'MTD A/T%'` 为例，单指标查询可编译为：

```sql
SELECT
  t.apmonth,
  AVG(CASE WHEN t.kpi_name = 'MTD A/T%' THEN t.kpi_value END) AS mtd_at_rate
FROM t_person_level_kpis_202606181333 AS t
WHERE t.role = 'DM'
GROUP BY t.apmonth;
```

这里用户筛选（如角色、月份、产品）进入 `WHERE`；指标定义中的 `kpi_name` 约束被安全地包含在该指标的表达式内。

### 2.2 不建议直接采用的部分

| 建议字段 | 问题 | 建议调整 |
| --- | --- | --- |
| `target_type` = 宽表 / 窄表 | “窄表”描述的是物理布局，不是指标业务语义；未来会出现 EAV、版本、情景、渠道、累计/当期等更多形态。 | 保留为 `source_shape`，但只用作编译策略提示，不作为业务逻辑主轴。 |
| `kpi where` | 若为自由文本，存在 SQL 注入、方言差异、字段重命名失效、LLM 误读及无法校验的问题。 | 改为结构化 `metric_filters`，由服务端按字段、操作符和值编译。 |
| KPI 值是 DISTINCT 下拉 | 可改善配置体验，但不能成为唯一模型：值会变化、数量可能很大、还可能需要多条件或 `IN`。 | 将其做成结构化条件编辑器中的一种值选择器，支持搜索、分页、回填原始值。 |
| 在 `WHERE` 统一加 `kpi_name = ...` | 一次查一个指标时可用；一次查询多个不同 KPI 时会产生冲突。 | 多指标查询使用条件聚合；只有单指标且安全时可下推为 `WHERE` 以改善性能。 |

特别地，下列写法对“同时查询两个不同 KPI”是错误的：

```sql
-- 错误：同一行无法同时满足两个不同的 KPI 名称
WHERE kpi_name = 'MTD A/T%' AND kpi_name = 'QTD A/T%'
```

正确形式是每个 Metric 各自持有筛选条件：

```sql
SELECT
  SUM(CASE WHEN t.kpi_name = '销售金额' THEN t.kpi_value END) AS sales_amount,
  SUM(CASE WHEN t.kpi_name = '销售数量' THEN t.kpi_value END) AS sales_quantity
FROM t_person_level_kpis_202606181333 AS t
WHERE t.apmonth = '2026AP04';
```

---

## 3. 推荐的成熟建模方式

### 3.1 核心原则：Measure 与 Metric 分离

成熟语义层通常不把“物理列、聚合、固定过滤、展示指标”混在一个自由 SQL 公式里，而是将它们显式建模：

- **事实来源（fact source / Class）**：表、事实粒度、关联实体；
- **维度（dimension）**：可分组、可由用户筛选的字段，如月份、角色、产品、人员；
- **度量值（measure）**：可聚合的原子数值字段，例如 `kpi_value`；
- **语义指标（metric）**：在特定事实来源上，对 measure 使用聚合规则，并叠加固定的、受控的筛选条件；
- **指标口径/规则**：单位、格式、空值策略、是否可加、适用粒度、版本与审批状态。

对宽表，Metric 是“聚合指定列”；对当前窄表，Metric 是“聚合共同的 `kpi_value`，但用指标级过滤器选择 `kpi_name` 的某个成员”。二者在逻辑模型中一致。

### 3.2 建议的 Metric 定义

推荐新增一个结构化字段，例如 `definition`；为兼容现有数据，可先新增独立列，最终再将自由 `formula` 降为兼容/高级表达式用途。

```json
{
  "source_class": "PersonLevelKpi",
  "source_shape": "long",
  "aggregation": "AVG",
  "value_field": "KPI指标值",
  "metric_filters": [
    {
      "field": "KPI名称",
      "operator": "=",
      "value": "MTD A/T%"
    }
  ],
  "value_type": "ratio",
  "display_format": "0.0%",
  "null_policy": "ignore",
  "grain": ["考核月份", "角色", "产品英文名称", "DM员工NT ID"],
  "additivity": "non_additive"
}
```

宽表的等价定义更简单：

```json
{
  "source_class": "ProductMoleculeSales",
  "source_shape": "wide",
  "aggregation": "SUM",
  "value_field": "销售数量",
  "metric_filters": [],
  "value_type": "quantity",
  "display_format": "#,##0",
  "null_policy": "ignore",
  "additivity": "additive"
}
```

#### 关键约束

1. 一个可执行 Metric 在第一阶段必须只属于一个 `source_class`。当前 `target_classes` 可保留为目录/权限标签，但不能作为公式解析时的歧义来源。
2. `value_field`、`metric_filters[].field` 必须是该 Class 中存在的**逻辑字段**，由服务端映射到物理列；禁止直接接收物理列名或 SQL 片段。
3. `metric_filters` 仅允许白名单操作符：`=`、`IN`、`!=`、`NOT IN`、`IS NULL`、`IS NOT NULL`；暂不允许任意 `LIKE`、函数或子查询。
4. 保存时必须验证：筛选值存在于当前源数据的 DISTINCT 值中（或由受控的管理员豁免）；值的实际类型与字段类型一致。
5. `aggregation` 不能由字段类型自动猜测。`SUM`、`AVG`、`COUNT_DISTINCT`、`MIN`、`MAX` 由指标口径明确选择；比例类 KPI 通常是 `AVG`、加权平均或“分子/分母重新计算”，并不天然可 `SUM`。

### 3.3 为什么这是比 `kpi where` 更成熟的方案

`metric_filters` 不是“为 KPI 名称写一个特例”，而是语义层中的 scoped filter / measure filter：

- 当前可表达 `KPI名称 = 'MTD A/T%'`；
- 后续可表达 `KPI名称 IN (...)`、`版本 = 'Actual'`、`渠道 = 'DTP'`；
- 可在管理端逐项校验、审计、回显、迁移和安全编译；
- 可复用现有 `DataQueryEngine._build_filter_clause()` 的字段映射与操作符白名单思想，但需要把指标筛选编译为**指标表达式的一部分**；
- 这与主流语义层把 dimensions、measures、metrics 及指标过滤分开描述的做法一致。

参考资料：

1. dbt Semantic Layer 概览与 Metrics 文档：<https://docs.getdbt.com/docs/build/semantic-layer/overview>、<https://docs.getdbt.com/docs/build/semantic-layer/metrics>
2. Cube 的数据建模与 Metrics/Dimensions 概念：<https://cube.dev/docs/product/data-modeling/concepts/metrics-and-dimensions>
3. Snowflake Metric Views 概览：<https://docs.snowflake.com/en/user-guide/snowflake-cortex/metric-layer/metric-views-overview>
4. Kimball Group 关于维度建模和事实表粒度的说明：<https://www.kimballgroup.com/data-warehouse-business-intelligence-resources/kimball-techniques/dimensional-modeling-techniques/>

这些资料共同强调：先声明事实粒度、维度和度量，再在语义层定义可复用、可治理的指标，而不是让报表或查询端拼接自由 SQL。

---

## 4. 查询编译设计

### 4.1 正确的编译顺序

对每个选择的 Metric：

1. 解析并校验 `source_class`、`value_field`、`aggregation`、`metric_filters`；
2. 将逻辑字段映射到物理字段；
3. 生成该 Metric 专属的筛选谓词；
4. 生成条件聚合表达式；
5. 将用户的问题筛选、行级权限筛选和共同维度筛选放入全局 `WHERE`；
6. 对最终的聚合结果应用 `HAVING` 与排序。

逻辑表达式为：

$$
M_i(g) = A_i\bigl(\operatorname{CASE\ WHEN}\ P_i(r)\ \operatorname{THEN}\ v_i(r)\ \operatorname{END}\bigr)
$$

其中：

- $g$ 是用户选择的分组维度；
- $A_i$ 是 Metric 定义的聚合函数；
- $P_i$ 是 Metric 固有筛选条件，例如 `kpi_name = 'MTD A/T%'`；
- $v_i$ 是 `kpi_value`；
- 用户临时条件不属于 $P_i$，而属于全局 `WHERE`。

### 4.2 SQL 生成策略

| 情形 | 推荐 SQL 策略 |
| --- | --- |
| 只选一个窄表 Metric | 可将其固定筛选下推到 `WHERE`，减少扫描；但语义上仍从 `metric_filters` 生成。 |
| 同源、多个窄表 Metric | 每个 Metric 使用一个 `CASE WHEN` 条件聚合，避免相互冲突。 |
| 同源、相同固定筛选的多个 Metric | 可安全将共同条件下推到 `WHERE`。 |
| 不同 source class 的 Metric | 按当前 join planner 处理；若事实粒度不同，禁止直接 JOIN 后聚合，改用按各自粒度预聚合的子查询再关联，或分开执行。 |
| 指标级条件与用户筛选同字段冲突 | 必须显式报出不可满足/结果为空的语义诊断，不可静默覆盖任一条件。 |

建议新增一个“编译计划”中间结构，避免在 `data_query` 散落 `if target_type == ...`：

```json
{
  "metric_id": "mtd_at_rate",
  "source_class": "PersonLevelKpi",
  "aggregation": "AVG",
  "value_field": "KPI指标值",
  "fixed_predicates": [
    {"field": "KPI名称", "operator": "=", "value": "MTD A/T%"}
  ],
  "alias": "mtd_at_rate"
}
```

`DataQueryEngine` 只负责把此受控结构编译为 SQL。这样后续支持 `COUNT_DISTINCT`、加权平均、分子/分母指标、期间对比时不会继续膨胀为“宽表/窄表/KPI 特例”的条件分支。

### 4.3 空值、数值与比例的处理

当前 CSV 的 `kpi_value` 存在空字符串。实施前需要明确并测试：

- 导入阶段把空字符串标准化为 `NULL`；
- 当物理存储是文本时，统一、安全地转换为数值；不要依赖数据库的隐式转换；
- `SUM` / `AVG` 应忽略 `NULL`，而不是把缺失 KPI 当作 $0$；
- 对比例指标，明确是 `AVG(kpi_value)`、加权平均，还是按分子/分母重算；
- 输出中同时提供非空样本量（例如 `COUNT(kpi_value)`）或数据质量告警，以防“所有值为空”被误解为 $0$。

---

## 5. 管理端设计建议

### 5.1 不建议的交互

不要提供一个名为“kpi where”的自由文本输入框。它会诱导管理员写 `kpi_name = 'xxx'`、数据库函数或物理列名，造成不可移植和不可验证的规则。

### 5.2 建议的“指标来源与口径”区域

1. **目标事实 Class**（单选，必填）；
2. **来源布局**：`wide` / `long`，默认从 Class 配置或字段结构推断；
3. **数值字段**（逻辑字段下拉，必填）；
4. **聚合方式**（下拉，必填）；
5. **指标固定条件**（可添加多行）：
   - 字段：当前 Class 的逻辑字段下拉；
   - 操作符：受限下拉；
   - 值：从对应字段 DISTINCT 值中可搜索、分页选择，回传原始值；
6. **数值类型与格式**：金额、数量、比例、评分；
7. **粒度与可加性**：由管理员明确标注并在保存时校验；
8. **公式**：一期保留为高级兼容字段，并明确标记“不能替代上述结构化定义”。

当选择 `source_shape = long` 时，可预填：

- 数值字段：`KPI指标值`；
- 固定条件字段：`KPI名称`；
- 值候选：`SELECT DISTINCT kpi_name ...` 的结果。

但预填不应成为硬编码；同一能力也应允许其他指标分类列与多个固定条件。

### 5.3 DISTINCT 值接口要求

建议新增一个受控元数据 API，而不是让前端读取整张 CSV：

`GET /api/scenarios/{scenario_id}/schema-classes/{class_id}/fields/{field}/values?q=&cursor=&limit=`

服务端应：

- 校验场景、Class、逻辑字段和访问权限；
- 使用 `schema_mapping.json` 将逻辑字段映射到物理列；
- 支持搜索、分页、最大返回数和缓存；
- 返回原始值与展示标签，不拼接 SQL；
- 记录数据版本/更新时间。若值域很大，前端必须搜索后加载，不能一次性下拉全部值。

---

## 6. 数据模型与治理前置工作

### 6.1 修复事实粒度与主键

`PersonLevelKpi` 的事实粒度应先被正式定义，例如“某月份、某产品、某角色层级中的某人员组织位置、某 KPI 名称的一次观测”。实际键需要数据剖析确认；不应使用单独的 `kpi_name`。

建议优先级：

1. 若源系统有稳定行 ID，使用它；
2. 否则在摄取时生成 `kpi_record_id` 作为技术主键；
3. 将可能的业务复合键写入 Class 元数据并运行唯一性检查；
4. 将 `kpi_name` 标记为指标标识维度，而不是主键。

### 6.2 先处理“事实表 vs 指标定义表”的混合

本表同时含有实际值（`kpi_value`、`kpi_light`）和指标定义/规则（`kpi_meaning`、红黄绿规则、重要性）。长期建议拆为：

- `fact_person_kpi_observation`：人员、月份、产品、KPI 标识、实际值、灯色；
- `dim_kpi_definition`：KPI 名称、分类、层级、业务含义、阈值、单位、可加性、版本有效期。

短期无需重构 CSV，也可以在语义层将两部分逻辑分开；但这个拆分是后续指标治理、口径版本控制、规则变更追溯的最佳目标模型。

### 6.3 Metric 的生命周期治理

指标定义建议具备：

- `draft / approved / deprecated` 状态；
- 生效日期、失效日期与口径版本；
- 数据负责人、业务负责人、变更原因；
- 语义测试状态与最近验证时间；
- 所有查询中记录所使用的 Metric ID 与版本。

这比让每个 KPI 名称直接成为一个无版本的公式更适合医药商业分析中的可审计需求。

---

## 7. 分阶段实施建议

### Phase 0：数据与语义纠偏（必须先做）

1. 对 `PersonLevelKpi` 做粒度/重复/空值/数值类型剖析；
2. 修正 `kpi_name` 被标记为主键的问题；
3. 明确每个 KPI 的聚合规则、单位、空值策略和可加性；
4. 明确 CSV 到查询引擎时的数值标准化方式。

**验收：**可证明事实主键合理；每个试点 KPI 有明确的业务口径，且空值不会被误算为零。

### Phase 1：最小可用的结构化指标定义

1. 新增 `source_shape`、`value_field`、`aggregation`、`metric_filters`、`value_type`、`display_format`；
2. 完成数据库迁移、Pydantic 模型、CRUD、场景同步、知识文件导入导出和 ontology context 透传；
3. 管理端增加“指标固定条件”编辑器与 DISTINCT 值搜索接口；
4. 保持旧 Metric 的 `formula` 路径不变，保证宽表向后兼容。

**验收：**可创建一个 `KPI名称 = 'MTD A/T%'` 的 Metric，并在重启、场景导出/导入后仍可正确执行。

### Phase 2：查询编译与正确性保障

1. 由 `metric_filters` 编译固定条件；
2. 对多个同源窄表指标使用条件聚合；
3. 为单指标实现安全的谓词下推优化；
4. 对冲突固定条件、未映射字段、值类型错误给出明确诊断；
5. 从 LLM 的可见上下文中展示 Metric 的业务描述与名称，但不让 LLM 输出固定筛选或 SQL。

**验收：**同一次查询能返回两个不同 KPI，且不会产生 `kpi_name = A AND kpi_name = B`；用户筛选、分组、`HAVING`、排序均正确。

### Phase 3：语义层增强

1. 支持半可加/不可加指标、比率、分子分母定义与时间智能；
2. 加入指标版本、有效期、审批和数据质量状态；
3. 将 KPI 定义维度化，提供指标目录与影响分析；
4. 评估将常用窄表 KPI 物化为宽的语义视图或预聚合表，以优化高频分析。

---

## 8. 测试矩阵

至少应覆盖以下自动化测试：

| 类别 | 必测场景 |
| --- | --- |
| 向后兼容 | 现有宽表 `SUM(字段)` Metric 的 SQL 和结果不变。 |
| 单窄表 KPI | `AVG(kpi_value)` + `kpi_name = X`，按月份/人员分组。 |
| 多窄表 KPI | 同一查询选择 $X$、$Y$ 两个 `kpi_name`，验证各列独立条件聚合。 |
| 用户过滤 | 用户按月份、产品、角色、人员过滤时不覆盖 Metric 固定条件。 |
| 聚合语义 | `SUM`、`AVG`、`COUNT_DISTINCT` 及比例指标的预期值。 |
| 空值 | 空 `kpi_value`、全空组、非数值输入与数值转换。 |
| HAVING/排序 | 对窄表 Metric 的大于、小于、排序及别名引用。 |
| 映射与安全 | 逻辑字段到物理列映射、非法字段/操作符/值被拒绝，不能注入 SQL。 |
| 数据源 | 内存 CSV 与外部数据库两种运行模式。 |
| 元数据链路 | 新字段经过 API、DB、schema 同步、知识文件导入、ontology 加载后不丢失。 |

---

## 9. 最终建议

采用下面的产品决策：

> **将当前需求实现为“指标级结构化筛选条件”，而不是“窄表 KPI 的自由 where 字符串”。**
>
> 在第一阶段，为管理端提供 `source_shape`、`value_field`、`aggregation` 和 `metric_filters`；在窄表场景中默认生成 `KPI名称 = <DISTINCT 值>`。查询引擎针对多 Metric 使用条件聚合。与此同时，先修正 `PersonLevelKpi` 的事实粒度和主键，并为每个 KPI 明确聚合与空值口径。

这条路径能以较小改动支持当前 Pfizer 窄表，同时建立可扩展、可验证、可审计的语义指标基础，避免未来每遇到一类表形就继续在 `data_query` 中新增专用分支。
