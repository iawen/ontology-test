"""
Chat v3 - 无状态子智能体
========================
设计原则：
  1. 严格无状态：子智能体不持有任何全局状态，只做计算
  2. 输入输出契约：明确输入参数和返回类型
  3. 可独立测试：每个子智能体可单独单元测试

子智能体清单：
  - SchemaRetrieverAgent: 动态检索相关 Schema
  - GlossaryMatcherAgent: 术语匹配
  - SkillRouterAgent: 技能路由
  - ContextCompressorAgent: 上下文压缩
  - EntityDisambiguatorAgent: 实体消歧（4级匹配）
  - ToolExecutor: 工具执行器（含后置自动校正）
"""

import re
import json
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass


# ============================================================
# Schema 检索 Agent
# ============================================================

class SchemaRetrieverAgent:
    """
    动态检索与用户问题相关的 Schema。
    解决问题1：Schema 信息爆炸。

    契约：
      输入: user_message, ontology_engine
      输出: {"context": str, "relevant_classes": list}
    """

    async def retrieve(self, user_message: str, ontology_engine) -> dict:
        keywords = self._extract_keywords(user_message)
        relevant_classes = self._find_relevant_classes(keywords, ontology_engine)
        relevant_metrics = self._find_relevant_metrics(keywords, ontology_engine)
        relevant_rels = self._find_relevant_relationships(relevant_classes, ontology_engine)

        context = self._build_context(
            ontology_engine, relevant_classes, relevant_metrics, relevant_rels
        )

        return {
            "context": context,
            "relevant_classes": relevant_classes,
        }

    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词（中文分词简化版）"""
        keywords = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z_]{3,}|\d+", text)
        return [k.lower() for k in keywords]

    def _find_relevant_classes(self, keywords: List[str], oe) -> List[str]:
        relevant = set()
        for c in oe.list_classes():
            class_text = f"{c.get('id','')} {c.get('name_cn','')} {c.get('description','')}".lower()
            for kw in keywords:
                if kw in class_text:
                    relevant.add(c["id"])
                    break
            # 字段名匹配
            for prop in c.get("properties", []):
                prop_lower = prop.lower()
                for kw in keywords:
                    if kw in prop_lower:
                        relevant.add(c["id"])
                        break
        return list(relevant)

    def _find_relevant_metrics(self, keywords: List[str], oe) -> List[str]:
        relevant = set()
        for m in oe.list_metrics():
            metric_text = f"{m.get('id','')} {m.get('name','')} {m.get('description','')} {m.get('category','')}".lower()
            for kw in keywords:
                if kw in metric_text:
                    relevant.add(m["id"])
                    break
        return list(relevant)

    def _find_relevant_relationships(self, class_ids: List[str], oe) -> List[dict]:
        relevant = []
        for rel in oe.relationships:
            if rel.get("source") in class_ids or rel.get("target") in class_ids:
                relevant.append(rel)
        return relevant

    def _build_context(self, oe, class_ids: List[str], metric_ids: List[str], rels: List[dict]) -> str:
        """构建精简上下文"""
        parts = []

        # 1. 全局摘要（始终包含）
        all_classes = [f"{c['id']}({c.get('name_cn','')})" for c in oe.list_classes()]
        parts.append(f"## 全局实体类清单\n{', '.join(all_classes)}")

        # 2. 相关 Class 详情
        if class_ids:
            parts.append("## 相关实体类详情")
            for c in oe.list_classes():
                if c["id"] in class_ids:
                    props = c.get("properties", [])[:10]
                    parts.append(f"- **{c['id']}**({c.get('name_cn','')}): {', '.join(props)}")

        # 3. 相关 Metric 详情
        if metric_ids:
            parts.append("## 相关指标详情")
            for m in oe.list_metrics():
                if m["id"] in metric_ids:
                    parts.append(f"- **{m['id']}**({m.get('name','')}): {m.get('formula','')}")

        # 4. 相关 Relationship
        if rels:
            parts.append("## 相关关系")
            for r in rels:
                parts.append(f"- {r['source']} --[{r.get('type','')}]--> {r['target']} (JOIN: {r.get('join_key','')})")

        return "\n\n".join(parts)


# ============================================================
# 术语匹配 Agent
# ============================================================

class GlossaryMatcherAgent:
    """
    匹配用户消息中的专用术语。

    契约：
      输入: scenario_id, user_message
      输出: [{"term": str, "standard_name": str, "description": str}]
    """

    async def match(self, scenario_id: str, user_message: str) -> List[dict]:
        from modules.glossary import match_glossary_terms
        return match_glossary_terms(scenario_id, user_message) or []


# ============================================================
# 技能路由 Agent
# ============================================================

class SkillRouterAgent:
    """路由用户消息到相关技能包（增加鲁棒性兜底）"""
    async def route(self, scenario_id: str, user_message: str) -> List[dict]:
        try:
            from modules.skills import route_skills
            res = await route_skills(scenario_id, user_message)
            
            # 如果返回的是字符串，尝试在 Agent 内部自解包
            if isinstance(res, str):
                try:
                    res = json.loads(res)
                except json.JSONDecodeError:
                    print(f"[SkillRouter] 警告: 底层返回了非法的 JSON 字符串: {res}")
                    return []
                    
            return res if isinstance(res, list) else []
        except Exception as e:
            # 容错降级：记录日志，返回空列表，确保主流程不中断
            print(f"[SkillRouter] 严重警告: 技能路由解析失败: {str(e)}")
            return []

# ============================================================
# 上下文压缩 Agent
# ============================================================

class ContextCompressorAgent:
    """
    压缩上下文，防止超出模型限制。

    契约：
      输入: context: str, limit: int
      输出: compressed_context: str
    """

    HARD_LIMIT = 20000

    async def compress(self, context: str, limit: int = None) -> str:
        limit = limit or self.HARD_LIMIT
        if len(context) <= limit:
            return context
        head = context[:int(limit * 0.75)]
        tail = context[-int(limit * 0.25):]
        return head + "\n\n[...上下文已压缩...]\n\n" + tail


# ============================================================
# 实体消歧 Agent
# ============================================================

class EntityDisambiguatorAgent:
    """
    实体消歧：在 SQL 生成前预查标准值。
    解决问题2：SQL 参数不对齐（如"江苏省"vs"江苏"）。

    契约：
      输入: user_message, relevant_classes, query_engine
      输出: [{"user_value": str, "standard_value": str, "field": str, "class_id": str}]
    """

    async def disambiguate(
        self, user_message: str, relevant_classes: List[str], query_engine
    ) -> List[dict]:
        hints = []
        for class_id in relevant_classes:
            text_fields = self._get_text_fields(class_id, query_engine)
            for field_name in text_fields:
                candidates = self._get_field_values(class_id, field_name, query_engine)
                if not candidates:
                    continue
                user_values = self._extract_entities(user_message, candidates)
                for uv in user_values:
                    best_match, score, all_cands = self._fuzzy_match(uv, candidates)
                    if best_match and score >= 0.75 and best_match != uv:
                        hints.append({
                            "user_value": uv,
                            "standard_value": best_match,
                            "field": field_name,
                            "class_id": class_id,
                            "similarity": score,
                            "all_candidates": all_cands[:5],
                        })
        return hints

    def _get_text_fields(self, class_id: str, qe) -> List[str]:
        oe = qe.oe
        field_types = oe.get_field_types(class_id)
        field_map = oe.get_field_map(class_id)
        return [
            logical for logical, ftype in field_types.items()
            if ftype == "text" and logical in field_map
        ]

    def _get_field_values(self, class_id: str, field_name: str, qe) -> List[str]:
        try:
            result = qe.fuzzy_search_values(class_id, field_name, "", limit=200)
            return result.get("matched_values", [])
        except:
            return []

    def _extract_entities(self, text: str, candidates: List[str]) -> List[str]:
        """从文本中提取可能的实体值"""
        entities = []
        for cand in candidates:
            if len(cand) >= 2 and cand in text:
                entities.append(cand)
        # 额外提取引号内内容
        quoted = re.findall(r'["\']([^"\']+)["\']', text)
        entities.extend(quoted)
        return list(set(entities))

    def _fuzzy_match(self, value: str, candidates: List[str]) -> Tuple[str, float, List[str]]:
        """4级模糊匹配"""
        value_clean = value.strip()
        suffixes = ["省", "市", "区", "县", "镇", "乡", "村", "公司", "有限", "有限公司"]
        value_core = value_clean
        for suffix in suffixes:
            if value_core.endswith(suffix):
                value_core = value_core[:-len(suffix)]

        best_match = ""
        best_score = 0.0
        scored = []

        for cand in candidates:
            cand_clean = cand.strip()
            score = 0.0
            if value_clean == cand_clean:
                score = 1.0
            elif value_core and value_core == cand_clean:
                score = 0.95
            elif value_core and value_core in cand_clean:
                score = 0.85
            elif cand_clean in value_clean:
                score = 0.80
            else:
                set1, set2 = set(value_clean), set(cand_clean)
                intersection = set1 & set2
                union = set1 | set2
                score = len(intersection) / len(union) if union else 0.0

            scored.append((cand, score))
            if score > best_score:
                best_score = score
                best_match = cand

        scored.sort(key=lambda x: x[1], reverse=True)
        return best_match, best_score, [s[0] for s in scored]


# ============================================================
# 工具执行器（含后置自动校正）
# ============================================================

class ToolExecutor:
    """
    工具执行器：执行工具调用，含后置自动校正。

    死循环防线：
      - 每次工具调用打上 retry_count 标记
      - 严格限制最大重试次数为 1 次
      - 再次失败走向 CLARIFY 或抛出异常
    """

    MAX_RETRY = 1  # 死循环防线

    def __init__(self, scenario_id: str, entity_agent: EntityDisambiguatorAgent):
        self.scenario_id = scenario_id
        self.entity_agent = entity_agent

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        query_engine,
        engine,  # OntologyEngine
        retry_count: int = 0,
    ) -> dict:
        """
        执行工具调用。

        Args:
            retry_count: 当前重试次数（死循环防线）
        """
        try:
            result = self._dispatch_tool(tool_name, arguments, query_engine, engine)

            # 后置自动校正：query_data 失败时尝试修正参数
            if (
                tool_name == "query_data"
                and result.get("error")
                and retry_count < self.MAX_RETRY
            ):
                corrected_args = await self._auto_correct_args(arguments, query_engine)
                if corrected_args != arguments:
                    return await self.execute(
                        tool_name, corrected_args, query_engine, engine, retry_count + 1
                    )

            return result

        except Exception as e:
            if retry_count < self.MAX_RETRY:
                return await self.execute(
                    tool_name, arguments, query_engine, engine, retry_count + 1
                )
            return {"error": f"工具执行失败（已重试{retry_count}次）: {str(e)}"}

    async def _auto_correct_args(self, arguments: dict, query_engine) -> dict:
        """后置自动校正：修正 query_data 的 filter 参数"""
        corrected = dict(arguments)
        filters = corrected.get("filters", [])
        target_class = corrected.get("target_class", "")

        new_filters = []
        for f in filters:
            field = f.get("field", "")
            value = f.get("value", "")
            # 尝试模糊匹配修正
            candidates = []
            try:
                result = query_engine.fuzzy_search_values(target_class, field, str(value), limit=50)
                candidates = result.get("matched_values", [])
            except:
                pass

            if candidates:
                best_match, score, _ = self.entity_agent._fuzzy_match(str(value), candidates)
                if best_match and score >= 0.75:
                    new_filters.append({**f, "value": best_match})
                else:
                    new_filters.append(f)
            else:
                new_filters.append(f)

        corrected["filters"] = new_filters
        return corrected

    def _dispatch_tool(self, name: str, args: dict, query_engine, engine) -> dict:
        """工具分发执行"""
        if name == "get_ontology_schema":
            class_id = args.get("class_id", "")
            if class_id:
                return query_engine.get_class_sample(class_id)
            else:
                classes_str = "\n".join([
                    f"  {c['id']}（{c['name_cn']}）→ {engine.classes.get(c['id'], {}).get('csv_file', '')}"
                    for c in engine.list_classes()
                ])
                rels_str = "\n".join([
                    f"  {r['source']} --[{r.get('type', '')}]--> {r['target']} (JOIN: {r.get('join_key', '')})"
                    for r in engine.relationships
                ])
                return {
                    "type": "schema_overview",
                    "classes": engine.list_classes(),
                    "relationships": engine.relationships,
                    "summary": f"实体类:\n{classes_str}\n\n关系:\n{rels_str}",
                }

        elif name == "query_ontology_data":
            return query_engine.execute_query(
                target_class=args.get("target_class", ""),
                metrics=args.get("metrics", []),
                dimensions=args.get("dimensions", []),
                filters=args.get("filters", []),
                join_class=args.get("join_class", ""),
                order_by=args.get("order_by", ""),
                limit=args.get("limit", 100),
                having=args.get("having", []),
            )

        elif name == "fuzzy_search_values":
            return query_engine.fuzzy_search_values(
                args.get("class_id", ""),
                args.get("field_name", ""),
                args.get("keyword", ""),
                args.get("limit", 10),
            )

        elif name == "get_class_sample":
            return query_engine.get_class_sample(args.get("class_id", ""), args.get("limit", 5))

        # elif name == "get_field_types":
        #     class_id = args.get("class_id", "")
        #     return {"class_id": class_id, "field_types": engine.get_field_types(class_id)}

        # elif name == "get_join_path":
        #     source = args.get("source", "")
        #     target = args.get("target", "")
        #     path = engine.get_join_path(source, target)
        #     return {"source": source, "target": target, "path": path}

        # elif name == "lookup_metric":
        #     from modules.metrics import lookup_metric
        #     return lookup_metric(self.scenario_id, args.get("metric_name", ""))

        elif name == "python_analyze":
            from tools.python_analyize import python_analyze
            return python_analyze(args.get("code", ""), args.get("df_data", []))

        # elif name == "list_available_actions":
        #     from modules.actions import get_available_actions
        #     return {"actions": get_available_actions(self.scenario_id)}

        # elif name == "execute_action":
        #     from modules.actions import _execute_action
        #     return _execute_action(
        #         self.scenario_id,
        #         args.get("action_id", ""),
        #         args.get("context", {}),
        #         args.get("confirmed", False),
        #     )

        else:
            return {"error": f"未知工具: {name}"}
