# ChatEngineV3 Cache 服务方案评审与开源项目集成分析

> **日期**: 2026-07-15
> **输入文档**: `ChatEngineV3_Cache_Service_Design.md`（用户提供的方案设计）
> **分析范围**: 方案合理性评审 + 开源项目匹配度评估 + 集成改造工作量分析

---

## 一、方案评审：需要调整的点

### 1.1 整体评价

用户提供的 Cache 方案设计**非常成熟**，核心原则完全正确：

- ✅ "以结构化查询计划为核心"而非"以 SQL 为核心"——这是 ChatBI 场景下最安全的缓存策略
- ✅ 三层复用（L1 精确结果 / L2 模板改写 / L3 语义问题）层次清晰
- ✅ 版本、数据新鲜度、权限范围作为硬约束——避免了错误复用
- ✅ `observe` 模式先行——工程化最佳实践
- ✅ 不绕过 `DataQueryEngine`、`HarnessSQL`、实体消歧——安全边界完整

### 1.2 需要调整的 5 个点

| # | 调整点 | 原方案 | 建议调整 | 理由 |
|---|--------|--------|---------|------|
| 1 | **L3 语义缓存的触发时机** | 在 `CONTEXT_PREP` 后、`SCHEMA_PLAN` 前做语义匹配 | 将 L3 检查提前到 `CONTEXT_PREP` 内部，与 Glossary/Schema 检索并行 | 语义匹配需要 Embedding 计算，与 Schema 检索并行可减少串行延迟；且如果 L3 命中，可以跳过后续的 LLM 规划调用，收益更大 |
| 2 | **L2 模板缓存的"可变槽位"定义** | 文档提到"补齐或改写可变槽位"但未明确定义 | 明确定义 4 类可变槽位：`time_range`、`filter_values`、`dimensions`、`order_by`，并规定每类槽位的改写规则 | 避免改写时引入不安全参数（如用户输入直接拼入 filter_values），需通过实体消歧后再写入 |
| 3 | **缓存失效的粒度** | 文档提到"本体变更时缓存失效"但未明确粒度 | 区分 3 级失效粒度：`Metric 级`（单个 Metric 定义变更）、`Class 级`（表结构/字段映射变更）、`Scenario 级`（整体本体重新提取） | 避免一次小改动导致全量缓存失效；Metric 级失效只需清除该 Metric 相关的 L1/L2 缓存 |
| 4 | **Plan-Execute 多子问题的缓存策略** | 文档提到"跨请求命中 L1"但未明确子问题级缓存 | Plan-Execute 的每个子问题应独立计算缓存 key，而非整体计划级缓存 | 同一计划内的不同子问题可能命中不同的历史缓存，独立 key 可以最大化复用率 |
| 5 | **缓存 Key 中的"权限范围"** | 文档提到"权限变化时不共享结果缓存"但未定义 key 结构 | 在 cache key 中增加 `permission_hash` 字段，由 `user_role + accessible_class_ids + accessible_metric_ids` 的有序哈希组成 | 确保不同角色即使查询参数相同，也不会交叉命中缓存 |

### 1.3 补充建议

**缓存 Key 的规范化时间处理**：
文档提到"相对时间边界"问题，建议增加一个 `TimeCanonicalizer` 组件：
- 将"本月"、"上月"、"Q1"等相对时间表达式解析为绝对时间范围（如 `2026-07-01~2026-07-31`）
- 绝对时间范围作为 cache key 的一部分，确保"月末前后本月"不会误命中
- 这个组件应复用已有的 `EntityDisambiguatorAgent` 中的 AP month/quarter 归一化逻辑

---

## 二、开源项目匹配度评估

### 2.1 候选项目概览

| 项目 | GitHub | 类型 | 匹配度 | 说明 |
|------|--------|------|--------|------|
| **GPTCache** | [zilliztech/GPTCache](https://github.com/zilliztech/GPTCache) | LLM 语义缓存 | ⭐⭐⭐⭐ | 最成熟的开源语义缓存，支持自定义 pre-processor/embedding/similarity |
| **Redis Semantic Cache** | [redis-developer/redis-ai-resources](https://github.com/redis-developer/redis-ai-resources) | Redis 向量缓存 | ⭐⭐⭐ | 基于 Redis + 向量搜索的语义缓存，适合 L3 层 |
| **ModelCache** | [codefuse-ai/ModelCache](https://github.com/codefuse-ai/ModelCache) | LLM 语义缓存 | ⭐⭐⭐ | 类似 GPTCache，但社区较小 |
| **Langfuse** | [langfuse/langfuse](https://github.com/langfuse/langfuse) | LLM 可观测性 | ⭐⭐ | 有 SDK 级 prompt 缓存，但不支持查询结果缓存 |
| **Databricks Semantic Caching** | [databricks-industry-solutions/semantic-caching](https://github.com/databricks-industry-solutions/semantic-caching) | 企业级语义缓存 | ⭐⭐ | 针对 Databricks 平台，通用性不足 |

### 2.2 推荐方案：GPTCache + Redis 混合架构

**核心结论**：没有单一开源项目能完全覆盖用户方案的三层缓存需求。推荐**混合架构**：

| 缓存层 | 推荐技术 | 理由 |
|--------|---------|------|
| **L1 精确结果缓存** | **Redis**（Hash + TTL） | 结构化查询参数的精确哈希匹配，Redis 的 KV 性能最优；TTL 天然支持数据新鲜度失效 |
| **L2 模板缓存** | **Redis**（Hash + Set 索引） | 以 `metric_id + scenario_id` 为索引，存储已验证的查询参数骨架；Redis Set 用于快速查找同 Metric 的所有模板 |
| **L3 语义问题缓存** | **GPTCache**（Embedding + 向量搜索） | GPTCache 的 `pre-processor` + `embedding` + `similarity evaluator` 架构天然适配 L3 的语义匹配需求 |

### 2.3 GPTCache 架构与用户方案的映射

GPTCache 的 6 大核心组件可以完美映射到用户方案：

| GPTCache 组件 | 用户方案对应 | 说明 |
|---------------|-------------|------|
| **Adapter** | `ChatEngineV3` 的 `stream_chat` 入口 | 拦截请求，先查缓存再走 LLM |
| **Pre-processor** | `CONTEXT_PREP` 阶段的 Glossary/Schema 检索 | 将用户问题预处理为标准化的查询意图 |
| **Embedding Generator** | L3 语义缓存的向量化 | 将"本月销售额"和"这个月的销售金额"映射到相近向量 |
| **Cache Manager** | L1/L2/L3 的统一管理 | 管理缓存生命周期、TTL、失效 |
| **Similarity Evaluator** | L3 的语义相似度判定 | 判断是否"高置信度且最终结构化参数一致" |
| **Post-processor** | 缓存命中后的结果适配 | 将缓存结果适配为当前请求的 SSE 格式 |

---

## 三、集成方案与改造工作量

### 3.1 整体架构

```
用户问题
    ↓
[ChatEngineV3.stream_chat]
    ↓
[CONTEXT_PREP] ──→ GPTCache.pre_processor（L3 语义匹配）
    │                    ↓ 命中？
    │               是 → [Similarity Evaluator] → 结构化参数一致？
    │                                    ↓ 是
    │                               [L1 精确结果检查] → 命中？
    │                                    ↓ 是
    │                               直接返回缓存结果
    │                                    ↓ 否
    │                               [L2 模板改写] → 改写后走正常校验链路
    │                    ↓ 未命中
    ↓
[SCHEMA_PLAN] → [QUERY_PLAN] → [TOOL_EXECUTE]
    ↓                                ↓
    ↓                        [缓存写入]
    ↓                        L1: 结构化参数哈希 → 结果
    ↓                        L2: metric_id → 参数骨架
    ↓                        L3: 问题 embedding → 意图+参数
    ↓
[FINAL_STREAM]
```

### 3.2 改造工作量估算

| 改造项 | 涉及文件 | 工作量 | 优先级 |
|--------|---------|--------|--------|
| **L1 精确结果缓存** | 新建 `cache/result_cache.py` + 修改 `engine.py` 的 `TOOL_EXECUTE` | 3天 | P0 |
| **L2 模板缓存** | 新建 `cache/template_cache.py` + 修改 `ontology_agent.py` | 3天 | P1 |
| **L3 语义缓存（GPTCache 集成）** | 新建 `cache/semantic_cache.py` + 修改 `engine.py` 的 `CONTEXT_PREP` | 4天 | P1 |
| **缓存 Key 规范化** | 新建 `cache/cache_key.py`（含 `TimeCanonicalizer`） | 2天 | P0 |
| **缓存失效管理** | 新建 `cache/invalidation.py` + 修改 `schema_optimizer.py` | 2天 | P1 |
| **`observe` 模式** | 修改 `engine.py` + `constants.py` | 1天 | P0 |
| **SSE 缓存命中标注** | 修改 `engine.py` 的 `_format_sse_event` | 0.5天 | P2 |
| **Plan-Execute 子问题级缓存** | 修改 `engine.py` 的 `_execute_metric_subquestion` | 1天 | P2 |
| **单元测试** | 新建 `tests/test_cache.py` | 2天 | P1 |
| **总计** | | **~18.5天** | |

### 3.3 集成步骤（推荐顺序）

#### Step 1：基础设施搭建（P0，~6天）

1. 安装 Redis 和 GPTCache：
```bash
pip install redis gptcache
```

2. 新建 `cache/` 目录结构：
```
cache/
├── __init__.py
├── cache_key.py          # 缓存 Key 生成与规范化
├── result_cache.py       # L1 精确结果缓存
├── template_cache.py     # L2 模板缓存
├── semantic_cache.py     # L3 语义缓存（GPTCache 集成）
├── invalidation.py       # 缓存失效管理
└── cache_config.py       # 缓存配置（模式、TTL、阈值）
```

3. 实现 `cache_key.py`：
```python
import hashlib
import json
from datetime import datetime

class CacheKeyBuilder:
    @staticmethod
    def build_l1_key(query_params: dict, scenario_id: str, permission_hash: str) -> str:
        """L1 精确结果缓存的 Key"""
        canonical = {
            "scenario_id": scenario_id,
            "target_class": query_params.get("target_class"),
            "metrics": sorted(query_params.get("metrics", [])),
            "dimensions": sorted(query_params.get("dimensions", [])),
            "filters": sorted(query_params.get("filters", []), key=lambda x: x.get("field", "")),
            "having": sorted(query_params.get("having", []), key=lambda x: x.get("field", "")),
            "join_classes": sorted(query_params.get("join_classes", [])),
            "order_by": query_params.get("order_by", ""),
            "permission_hash": permission_hash,
            "ontology_version": query_params.get("_ontology_version", ""),
        }
        return "l1:" + hashlib.sha256(
            json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    @staticmethod
    def build_l2_key(metric_id: str, scenario_id: str) -> str:
        """L2 模板缓存的 Key"""
        return f"l2:template:{scenario_id}:{metric_id}"

    @staticmethod
    def build_l3_key(question_embedding: list[float]) -> str:
        """L3 语义缓存的 Key（向量 ID）"""
        return "l3:semantic"
```

4. 实现 `result_cache.py`（L1）：
```python
import redis
import json
from .cache_key import CacheKeyBuilder

class ResultCache:
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis = redis.from_url(redis_url)
        self.default_ttl = 300  # 5 分钟

    def get(self, query_params: dict, scenario_id: str, permission_hash: str) -> dict | None:
        key = CacheKeyBuilder.build_l1_key(query_params, scenario_id, permission_hash)
        raw = self.redis.get(key)
        if raw:
            return json.loads(raw)
        return None

    def set(self, query_params: dict, scenario_id: str, permission_hash: str, 
            result: dict, ttl: int = None):
        key = CacheKeyBuilder.build_l1_key(query_params, scenario_id, permission_hash)
        self.redis.setex(key, ttl or self.default_ttl, json.dumps(result, ensure_ascii=False))
```

#### Step 2：L1 精确结果缓存集成（P0，~3天）

在 `engine.py` 的 `_handle_tool_execute` 中增加缓存检查：

```python
async def _handle_tool_execute(self, state: AgentState) -> State:
    # ... 现有代码 ...

    # 在执行查询前检查 L1 缓存
    if state.planned_query_args and not state.query_executed:
        if self.cache_config.mode != "disabled":
            permission_hash = self._compute_permission_hash(state)
            cached = self.result_cache.get(
                state.planned_query_args, state.agent_id, permission_hash
            )
            if cached and self.cache_config.mode == "result_enabled":
                state.sse_events.append({
                    "type": "cache_hit",
                    "layer": "L1",
                    "cache_key": "exact_result",
                })
                # 直接使用缓存结果，跳过 SQL 执行
                state.all_tool_results.append(cached)
                state.query_executed = True
                return State.FINAL_STREAM  # 或 LLM_CALL

        # 未命中缓存，走正常执行
        return await self._execute_validated_query_plan(state, executor, query_engine, engine)
```

#### Step 3：L3 语义缓存集成（P1，~4天）

```python
from gptcache import cache
from gptcache.adapter import openai
from gptcache.processor.pre import last_content_without_prompt
from gptcache.embedding import Onnx
from gptcache.similarity_evaluation import SearchDistanceEvaluation

class SemanticCache:
    def __init__(self):
        onnx = Onnx()
        cache.init(
            pre_embedding_func=last_content_without_prompt,
            embedding_func=onnx.to_embeddings,
            similarity_evaluation=SearchDistanceEvaluation(),
            data_manager=self._init_data_manager(),
        )

    def get(self, user_message: str, scenario_id: str) -> dict | None:
        """查询语义缓存"""
        # 使用 GPTCache 的相似度评估
        # 但不直接返回缓存回答，而是返回"意图+Metric+结构化参数模板"
        pass

    def put(self, user_message: str, intent_data: dict, scenario_id: str):
        """写入语义缓存"""
        pass
```

#### Step 4：缓存失效管理（P1，~2天）

```python
class CacheInvalidator:
    def __init__(self, redis_client):
        self.redis = redis_client

    def invalidate_metric(self, scenario_id: str, metric_id: str):
        """Metric 级失效：清除该 Metric 的所有 L1/L2 缓存"""
        # L2: 直接删除模板
        self.redis.delete(f"l2:template:{scenario_id}:{metric_id}")
        # L1: 扫描并删除包含该 metric_id 的结果缓存
        for key in self.redis.scan_iter(f"l1:*{metric_id}*"):
            self.redis.delete(key)

    def invalidate_class(self, scenario_id: str, class_id: str):
        """Class 级失效：清除该 Class 相关的所有缓存"""
        for key in self.redis.scan_iter(f"l1:*{class_id}*"):
            self.redis.delete(key)
        for key in self.redis.scan_iter(f"l2:*{class_id}*"):
            self.redis.delete(key)

    def invalidate_scenario(self, scenario_id: str):
        """Scenario 级失效：清除该场景的所有缓存"""
        for key in self.redis.scan_iter(f"l1:*{scenario_id}*"):
            self.redis.delete(key)
        for key in self.redis.scan_iter(f"l2:*{scenario_id}*"):
            self.redis.delete(key)
```

---

## 四、开源项目无法覆盖的部分（需自研）

| 能力 | 开源项目支持 | 需自研原因 |
|------|------------|-----------|
| **L1 精确结果缓存** | ❌ 无直接支持 | GPTCache 专注语义缓存，不支持结构化参数精确哈希；需基于 Redis 自研 |
| **L2 模板改写** | ❌ 无直接支持 | GPTCache 不支持"部分匹配+槽位改写"；需自研模板存储和改写逻辑 |
| **缓存 Key 规范化** | ❌ 无直接支持 | 时间规范化（AP month/quarter）、权限哈希等是 ChatBI 特有需求 |
| **缓存失效管理** | ❌ 无直接支持 | GPTCache 只有 TTL 失效，不支持基于本体变更的语义失效 |
| **Plan-Execute 子问题级缓存** | ❌ 无直接支持 | 多子问题独立缓存是 Plan-Execute 架构特有需求 |
| **`observe` 模式** | ❌ 无直接支持 | GPTCache 无"只观察不返回"模式 |

---

## 五、总结与建议

### 5.1 方案评审结论

用户的 Cache 方案设计**非常优秀**，只需调整 5 个点（L3 触发时机、L2 槽位定义、失效粒度、子问题缓存、权限 Key），即可进入实施。

### 5.2 开源项目集成结论

- **没有单一开源项目能完全覆盖**三层缓存需求
- **推荐混合架构**：Redis（L1/L2）+ GPTCache（L3）
- **GPTCache 的 Pre-processor/Embedding/Similarity 架构**与用户方案的 L3 层高度匹配
- **约 40% 的代码可复用开源组件**（Embedding 生成、向量搜索、相似度评估），**60% 需自研**（L1/L2 缓存逻辑、Key 规范化、失效管理、模板改写）

### 5.3 实施建议

1. **先做 L1**（3天）：收益最高、风险最低、不依赖 GPTCache
2. **再做 L2**（3天）：复用 L1 的 Redis 基础设施
3. **最后做 L3**（4天）：集成 GPTCache，先以 `observe` 模式运行
4. **全程保持 `observe` 模式**：收集真实命中分布后再开启 `result_enabled`
5. **总工作量约 18.5 天**（含测试），其中 GPTCache 集成约 4 天
