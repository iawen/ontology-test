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

from tools.logger import logger


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
        relevant_classes = self._merge_metric_target_classes(
            relevant_classes, relevant_metrics, ontology_engine
        )
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

    def _merge_metric_target_classes(self, class_ids: List[str], metric_ids: List[str], oe) -> List[str]:
        merged = set(class_ids)
        for metric in oe.list_metrics():
            if metric.get("id") in metric_ids:
                target_class = metric.get("target_class") or metric.get("class_id")
                if target_class:
                    merged.add(target_class)
        return list(merged)

    def _find_relevant_relationships(self, class_ids: List[str], oe) -> List[dict]:
        relevant = []
        for rel in oe.relationships:
            if rel.get("source") in class_ids or rel.get("target") in class_ids:
                relevant.append(rel)
        return relevant

    def _build_context(self, oe, class_ids: List[str], metric_ids: List[str], rels: List[dict]) -> str:
        """构建精简上下文"""
        parts = []
        metrics_by_class = self._group_metrics_by_class(oe.list_metrics())

        # 1. 全局摘要（始终包含）
        all_classes = [f"{c['id']}({c.get('name_cn','')})" for c in oe.list_classes()]
        parts.append(f"## 全局实体类清单\n{', '.join(all_classes)}")

        # 2. 相关 Class 详情
        if class_ids:
            parts.append("## 相关实体类、字段与指标")
            for c in oe.list_classes():
                if c["id"] in class_ids:
                    props = c.get("properties", [])[:10]
                    class_metrics = metrics_by_class.get(c["id"], [])
                    lines = [
                        f"- **{c['id']}**({c.get('name_cn','')})",
                        f"  字段: {', '.join(props) or '（暂无）'}",
                    ]
                    if class_metrics:
                        lines.append("  关联指标:")
                        for metric in class_metrics:
                            lines.append(f"    {self._format_metric(metric)}")
                    else:
                        lines.append("  关联指标: （暂无）")
                    parts.append("\n".join(lines))

        # 3. 命中但未归属到相关实体的 Metric
        if metric_ids:
            unlisted_metrics = []
            for m in oe.list_metrics():
                if m["id"] in metric_ids and m.get("target_class") not in class_ids:
                    unlisted_metrics.append(self._format_metric(m))
            if unlisted_metrics:
                parts.append("## 其他命中指标\n" + "\n".join(unlisted_metrics))

        # 4. 相关 Relationship
        if rels:
            parts.append("## 相关关系")
            for r in rels:
                parts.append(f"- {r['source']} --[{r.get('type','')}]--> {r['target']} (JOIN: {r.get('join_key','')})")

        return "\n\n".join(parts)

    @staticmethod
    def _group_metrics_by_class(metrics: List[dict]) -> Dict[str, List[dict]]:
        grouped: Dict[str, List[dict]] = {}
        for metric in metrics:
            target_class = metric.get("target_class") or metric.get("class_id") or "__unbound__"
            grouped.setdefault(target_class, []).append(metric)
        return grouped

    @staticmethod
    def _format_metric(metric: dict) -> str:
        name = metric.get("name") or metric.get("name_cn") or metric.get("id") or ""
        formula = metric.get("formula") or metric.get("calculation") or ""
        dimensions = metric.get("dimensions") or []
        if isinstance(dimensions, str):
            try:
                dimensions = json.loads(dimensions)
            except json.JSONDecodeError:
                dimensions = [dimensions]
        return (
            f"- **{name}** (`{metric.get('id', '')}`)"
            f" | 说明: {metric.get('description', '') or '-'}"
            f" | 计算: {formula or '-'}"
            f" | 维度: {', '.join(dimensions) if dimensions else '-'}"
        )


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
                    logger.warning("Skill route returned invalid JSON: %s", res)
                    return []
                    
            return res if isinstance(res, list) else []
        except Exception as e:
            # 容错降级：记录日志，返回空列表，确保主流程不中断
            logger.exception("Skill route failed: scenario_id=%s error=%s", scenario_id, str(e))
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
        lines = context.splitlines()
        head_limit = int(limit * 0.75)
        tail_limit = int(limit * 0.25)
        head_lines = []
        head_size = 0
        for line in lines:
            if head_size + len(line) + 1 > head_limit:
                break
            head_lines.append(line)
            head_size += len(line) + 1

        tail_lines = []
        tail_size = 0
        for line in reversed(lines):
            if tail_size + len(line) + 1 > tail_limit:
                break
            tail_lines.append(line)
            tail_size += len(line) + 1
        head = "\n".join(head_lines)
        tail = "\n".join(reversed(tail_lines))
        return head + "\n\n[...上下文已压缩...]\n\n" + tail


# ============================================================
# 实体消歧 Agent
# ============================================================

class EntityDisambiguatorAgent:
    """
    实体消歧：在 SQL 生成前预查标准值。
    解决问题2：SQL 参数不对齐

    契约：
      输入: user_message, relevant_classes, query_engine
      输出: [{"user_value": str, "standard_value": str, "field": str, "class_id": str}]
    """

    async def disambiguate(
        self, user_message: str, relevant_classes: List[str], query_engine
    ) -> List[dict]:
        hints = []
        mentions = self._extract_entity_mentions(user_message)
        if not mentions:
            return hints
        for class_id in relevant_classes:
            text_fields = self._get_text_fields(class_id, query_engine)
            for field_name in text_fields:
                for uv in mentions:
                    candidates = self._get_field_values(class_id, field_name, query_engine, uv)
                    if not candidates:
                        continue
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

    def _get_field_values(self, class_id: str, field_name: str, qe, keyword: str) -> List[str]:
        try:
            result = qe.fuzzy_search_values(class_id, field_name, keyword, limit=30)
            return result.get("matched_values") or result.get("values", [])
        except:
            return []

    def _extract_entity_mentions(self, text: str) -> List[str]:
        quoted = re.findall(r'["\']([^"\']+)["\']', text)
        raw_terms = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,}", text)
        mentions = []
        for item in quoted + raw_terms:
            for mention in self._mention_variants(item.strip()):
                if mention not in mentions:
                    mentions.append(mention)
        return mentions[:12]

    def _mention_variants(self, value: str) -> List[str]:
        if len(value) < 2:
            return []
        variants = [value]
        if re.fullmatch(r"[\u4e00-\u9fff]{3,}", value):
            max_window = min(6, len(value))
            for size in range(2, max_window + 1):
                for start in range(0, len(value) - size + 1):
                    variants.append(value[start:start + size])
        return variants

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
        value_core = self._normalize_match_core(value_clean)
        if not value_core:
            return "", 0.0, []

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

    def _normalize_match_core(self, value: str) -> str:
        value = re.sub(r"\s+", "", value or "")
        value = re.sub(r"(有限公司|有限责任公司|股份有限公司|集团|公司)$", "", value)
        value = re.sub(r"(?<=[\u4e00-\u9fff])(省|市|区|县|镇|乡|村)$", "", value)
        return value


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
            if tool_name == "query_ontology_data":
                arguments = await self._deterministic_pre_process(arguments, query_engine, engine)

            result = self._dispatch_tool(tool_name, arguments, query_engine, engine)

            # 后置自动校正：query_ontology_data 失败时尝试修正参数
            if (
                tool_name == "query_ontology_data"
                and result.get("error")
                and retry_count < self.MAX_RETRY
            ):
                corrected_args = await self._auto_correct_args(arguments, query_engine)
                if corrected_args != arguments:
                    logger.info(
                        "Tool args auto-corrected: scenario_id=%s tool=%s original=%s corrected=%s",
                        self.scenario_id,
                        tool_name,
                        json.dumps(arguments, ensure_ascii=False, default=str)[:1000],
                        json.dumps(corrected_args, ensure_ascii=False, default=str)[:1000],
                    )
                    return await self.execute(
                        tool_name, corrected_args, query_engine, engine, retry_count + 1
                    )

            return result

        except Exception as e:
            if retry_count < self.MAX_RETRY:
                logger.warning(
                    "Tool execution failed, retrying: scenario_id=%s tool=%s retry=%d error=%s",
                    self.scenario_id,
                    tool_name,
                    retry_count + 1,
                    str(e),
                )
                return await self.execute(
                    tool_name, arguments, query_engine, engine, retry_count + 1
                )
            logger.exception(
                "Tool execution failed after retries: scenario_id=%s tool=%s error=%s",
                self.scenario_id,
                tool_name,
                str(e),
            )
            return {"error": f"工具执行失败（已重试{retry_count}次）: {str(e)}"}

    async def _deterministic_pre_process(self, arguments: dict, query_engine, engine) -> dict:
        """前置确定性拦截：过滤值对齐与基础类型防御"""
        corrected = dict(arguments or {})
        target_class = corrected.get("target_class", "")
        filters = corrected.get("filters") or []
        having = list(corrected.get("having") or [])
        corrected_filters = []

        for item in filters:
            if not isinstance(item, dict):
                corrected_filters.append(item)
                continue
            field = item.get("field", "")
            if engine.get_metric_info(field):
                having.append(dict(item))
                logger.warning(
                    "Metric filter moved to HAVING before execution: scenario_id=%s field=%s filter=%s",
                    self.scenario_id,
                    field,
                    json.dumps(item, ensure_ascii=False, default=str)[:500],
                )
                continue
            class_id = engine.find_class_by_field(field) or target_class
            field_type = engine.get_field_type(class_id, field)
            value = self._coerce_filter_value(item.get("value"), field_type)
            fixed = {**item, "value": value}

            if field_type == "text" and isinstance(value, str) and value.strip():
                aligned = await self._align_text_filter_value(query_engine, class_id, field, value)
                if aligned and aligned != value:
                    fixed["value"] = aligned
                    fixed["_intercepted"] = True
            corrected_filters.append(fixed)

        corrected["filters"] = corrected_filters
        corrected["having"] = having
        return corrected

    def _coerce_filter_value(self, value, field_type: str):
        if isinstance(value, list):
            return [self._coerce_filter_value(item, field_type) for item in value]
        if value is None:
            return value
        if field_type == "numeric" and isinstance(value, str):
            cleaned = value.replace(",", "").strip()
            try:
                return float(cleaned) if "." in cleaned else int(cleaned)
            except ValueError:
                return value
        if field_type == "boolean" and isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "y", "是"):
                return True
            if lowered in ("false", "0", "no", "n", "否"):
                return False
        return value

    async def _align_text_filter_value(self, query_engine, class_id: str, field: str, value: str) -> Optional[str]:
        try:
            result = query_engine.fuzzy_search_values(class_id, field, value, limit=20)
            candidates = result.get("matched_values") or result.get("values", [])
        except Exception:
            return None
        best_match, score, _ = self.entity_agent._fuzzy_match(value, candidates)
        return best_match if best_match and score >= 0.75 else None

    async def _auto_correct_args(self, arguments: dict, query_engine) -> dict:
        """后置自动校正：修正 query_ontology_data 的 filter 参数"""
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
                candidates = result.get("matched_values") or result.get("values", [])
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
        if name == "query_ontology_data":
            if args.get("limit") is not None:
                logger.warning(
                    "Ignoring limit for query_ontology_data to avoid incomplete analysis: scenario_id=%s limit=%s",
                    self.scenario_id,
                    args.get("limit"),
                )
            return query_engine.execute_query(
                target_class=args.get("target_class", ""),
                metrics=args.get("metrics", []),
                dimensions=args.get("dimensions", []),
                filters=args.get("filters", []),
                join_classes=args.get("join_classes", []),
                order_by=args.get("order_by", ""),
                limit=None,
                having=args.get("having", []),
            )

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
            from agents.tools.python_analyize import python_analyze
            query_history = args.get("query_history", [])
            all_query_data = json.dumps(query_history, ensure_ascii=False, default=str)
            last_result = query_history[-1].get("result", []) if query_history else []
            data_json = json.dumps(last_result, ensure_ascii=False, default=str)
            logger.info("Python analyze started: scenario_id=%s query_history=%d", self.scenario_id, len(query_history))
            return python_analyze(
                code=args.get("code", ""),
                data_json=data_json,
                all_query_data=all_query_data,
            )

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
