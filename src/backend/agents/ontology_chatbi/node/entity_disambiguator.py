import json
import re
from collections import Counter
from datetime import datetime

from agents.ontology_chatbi.constants import (
    BARE_QUARTER_VALUE_PATTERN,
    CHINESE_QUARTER_VALUE_PATTERN,
    QUARTER_VALUE_PATTERN,
)
from agents.ontology_chatbi.helper import metric_target_classes
from tools.logger import logger
from core.llm.chat_model import get_async_client, get_model_name
from agents.ontology_chatbi.helper import ap_month_to_quarter, current_quarter_ap_months, valid_ap_month

# ============================================================
# 实体消歧 Agent
# ============================================================


class EntityDisambiguatorAgent:
    """
        query_ontology_data 参数对齐与实体值消岐。

    契约：
            输入: query_ontology_data 参数、OntologyEngine、DataQueryEngine
            输出: 对齐后的 query_ontology_data 参数或校验错误
    """

    LLM_CANDIDATE_LIMIT = 40
    LLM_MATCH_THRESHOLD = 0.75

    def _get_field_values(
        self,
        class_id: str,
        field_name: str,
        qe,
        keyword: str,
        cache: dict[tuple[str, str, str], list[str]],
    ) -> list[str]:
        cache_key = (class_id, field_name, keyword)
        if cache_key in cache:
            return cache[cache_key]
        try:
            result = qe.fuzzy_search_values(class_id, field_name, keyword, limit=30)
            values = result.get("matched_values") or result.get("values", [])
            if not values and hasattr(qe, "get_field_distinct_values"):
                result = qe.get_field_distinct_values(class_id, field_name, limit=200)
                values = result.get("matched_values") or result.get("values", [])
            cache[cache_key] = values
            return values
        except Exception as exc:
            logger.info(
                "Entity field values unavailable: class=%s field=%s keyword=%s error=%s",
                class_id,
                field_name,
                keyword,
                str(exc),
            )
            cache[cache_key] = []
            return []

    def _fuzzy_match(self, value: str, candidates: list[str]) -> tuple[str, float, list[str]]:
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
            elif self._is_ordered_subsequence(value_core, cand_clean):
                score = 0.78
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

    async def _match_value(
        self,
        value: str,
        candidates: list[str],
        field_name: str = "",
        class_id: str = "",
        user_message: str = "",
        selection_context: dict | None = None,
    ) -> tuple[str, float, list[str]]:
        best_match, score, all_cands = self._fuzzy_match(value, candidates)
        if best_match and score >= self.LLM_MATCH_THRESHOLD:
            return best_match, score, all_cands

        llm_match, llm_score = await self._llm_select_candidate(
            value,
            all_cands[: self.LLM_CANDIDATE_LIMIT],
            field_name=field_name,
            class_id=class_id,
            user_message=user_message,
            selection_context=selection_context,
        )
        if llm_match and llm_score >= self.LLM_MATCH_THRESHOLD:
            ranked = [llm_match, *[candidate for candidate in all_cands if candidate != llm_match]]
            return llm_match, llm_score, ranked
        return best_match, score, all_cands

    async def _llm_select_candidate(
        self,
        value: str,
        candidates: list[str],
        field_name: str = "",
        class_id: str = "",
        user_message: str = "",
        selection_context: dict | None = None,
    ) -> tuple[str, float]:
        normalized_candidates = [str(candidate) for candidate in candidates if str(candidate).strip()]
        if not value or not normalized_candidates:
            return "", 0.0
        try:
            

            logger.info(
                "Entity semantic candidate selection started: class=%s field=%s value=%s candidate_count=%d",
                class_id,
                field_name,
                value,
                len(normalized_candidates),
            )
            client = get_async_client()
            response = await client.chat.completions.create(
                model=get_model_name(),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是数据库实体值对齐器。结合已有查询选择，判断待确认过滤值是否等价于候选值中的某一项，"
                            "可考虑中英文翻译、缩写、别名、大小写、后缀省略等表达差异。"
                            "只能从候选值中原样选择；不确定时 match 输出空字符串。"
                            '只输出 JSON：{"match": string, "confidence": number}。'
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "filter_value": value,
                                "field_name": field_name,
                                "class_id": class_id,
                                "query_selection": selection_context or {},
                                "user_message": user_message,
                                "candidates": normalized_candidates,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0,
                max_tokens=200,
            )
            payload = self._parse_llm_json(response.choices[0].message.content or "{}")
            match = str(payload.get("match") or "") if isinstance(payload, dict) else ""
            confidence = float(payload.get("confidence") or 0) if isinstance(payload, dict) else 0.0
            if match in normalized_candidates:
                logger.info(
                    "Entity semantic candidate selected: class=%s field=%s value=%s match=%s confidence=%.3f",
                    class_id,
                    field_name,
                    value,
                    match,
                    confidence,
                )
                return match, min(max(confidence, 0.0), 1.0)
            logger.info(
                "Entity semantic candidate not selected: class=%s field=%s value=%s raw_match=%s confidence=%.3f",
                class_id,
                field_name,
                value,
                match,
                confidence,
            )
        except Exception as exc:
            logger.warning(
                "Entity semantic candidate selection failed: class=%s field=%s value=%s error=%s",
                class_id,
                field_name,
                value,
                str(exc),
            )
        return "", 0.0

    @staticmethod
    def _parse_llm_json(content: str) -> dict:
        text = str(content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _is_ordered_subsequence(value: str, candidate: str) -> bool:
        if len(value) < 2 or not candidate:
            return False
        cursor = 0
        for char in candidate:
            if cursor < len(value) and value[cursor] == char:
                cursor += 1
        return cursor == len(value)

    def _normalize_match_core(self, value: str) -> str:
        value = re.sub(r"\s+", "", value or "")
        value = re.sub(r"(有限公司|有限责任公司|股份有限公司|集团|公司)$", "", value)
        value = re.sub(r"(?<=[\u4e00-\u9fff])(省|市|区|县|镇|乡|村)$", "", value)
        return value

    async def prepare_query_ontology_data_args(
        self,
        arguments: dict,
        query_engine,
        engine,
        scenario_id: str = "",
    ) -> dict:
        """统一的 query_ontology_data 参数对齐入口。"""
        corrected = await self._deterministic_pre_process(arguments, query_engine, engine, scenario_id)
        return corrected

    async def auto_correct_query_ontology_data_args(
        self,
        arguments: dict,
        query_engine,
        engine,
        scenario_id: str = "",
    ) -> dict:
        """query_ontology_data 失败后的参数再对齐入口。"""
        corrected = dict(arguments)
        target_class = str(corrected.get("target_class") or "")
        allowed_classes = self._query_scope_classes(corrected, target_class)
        query_selection = self._query_selection_context(corrected)
        new_filters = []
        field_replacements: dict[str, str] = {}
        for item in corrected.get("filters") or []:
            if not isinstance(item, dict):
                new_filters.append(item)
                continue
            field = str(item.get("field") or "")
            value = item.get("value", "")

            if isinstance(value, str) and value.strip():
                class_id, _ = self._resolve_filter_field_type(
                    engine,
                    target_class,
                    field,
                    query_engine=query_engine,
                    allowed_classes=allowed_classes,
                )
                fixed_filter = await self._disambiguate_text_filter(
                    query_engine,
                    item,
                    value,
                    target_class=target_class,
                    class_id=class_id,
                    allowed_classes=allowed_classes,
                    query_selection=query_selection,
                    scenario_id=scenario_id,
                )
                fixed_field = str(fixed_filter.get("field") or "")
                if field and fixed_field and fixed_field != field:
                    field_replacements[field] = fixed_field
                new_filters.append(fixed_filter)
                continue

            candidate_class, candidates = self._value_candidates_for_filter(
                query_engine,
                target_class,
                field,
                str(value),
                allowed_classes=allowed_classes,
                scenario_id=scenario_id,
            )
            if candidates:
                best_match, score, _ = await self._match_value(
                    str(value),
                    candidates,
                    field_name=field,
                    class_id=candidate_class,
                    selection_context=query_selection,
                )
                new_filters.append({**item, "value": best_match} if best_match and score >= 0.75 else item)
            else:
                new_filters.append(item)

        corrected["filters"] = new_filters
        if field_replacements:
            corrected["dimensions"] = self._replace_dimensions(corrected.get("dimensions"), field_replacements)
        return corrected

    async def _deterministic_pre_process(self, arguments: dict, query_engine, engine, scenario_id: str = "") -> dict:
        corrected = dict(arguments or {})
        target_class = corrected.get("target_class", "") or self._infer_target_class(corrected, engine)
        if target_class:
            corrected["target_class"] = target_class
        allowed_classes = self._query_scope_classes(corrected, target_class)
        query_selection = self._query_selection_context(corrected)
        filters = corrected.get("filters") or []
        having = list(corrected.get("having") or [])
        corrected_filters = []
        field_replacements: dict[str, str] = {}

        for item in filters:
            if not isinstance(item, dict):
                corrected_filters.append(item)
                continue
            item = self._normalize_quarter_filter(item, target_class, engine, scenario_id)
            field = item.get("field", "")
            if engine.get_metric_info(field):
                having.append(dict(item))
                logger.warning(
                    "Metric filter moved to HAVING before execution: scenario_id=%s field=%s filter=%s",
                    scenario_id,
                    field,
                    json.dumps(item, ensure_ascii=False, default=str)[:500],
                )
                continue
            class_id, field_type = self._resolve_filter_field_type(
                engine,
                target_class,
                field,
                query_engine=query_engine,
                allowed_classes=allowed_classes,
            )
            value = self._coerce_filter_value(item.get("value"), field_type)
            fixed = {**item, "value": value}

            if field_type == "text":
                fixed = await self._disambiguate_text_filter(
                    query_engine,
                    item,
                    value,
                    target_class=target_class,
                    class_id=class_id,
                    allowed_classes=allowed_classes,
                    query_selection=query_selection,
                    scenario_id=scenario_id,
                )
                fixed_field = str(fixed.get("field") or "")
                if field and fixed_field and fixed_field != field:
                    field_replacements[str(field)] = fixed_field
            corrected_filters.append(fixed)

        corrected["filters"] = corrected_filters
        if field_replacements:
            corrected["dimensions"] = self._replace_dimensions(corrected.get("dimensions"), field_replacements)
        corrected["having"] = having
        return corrected

    @staticmethod
    def _replace_dimensions(dimensions, field_replacements: dict[str, str]):
        if not isinstance(dimensions, list):
            return dimensions
        return [field_replacements.get(str(field), field) for field in dimensions]

    @staticmethod
    def _query_selection_context(arguments: dict) -> dict:
        """当前模型已选择的查询参数，不含用户原始问题。"""
        return {
            key: arguments.get(key)
            for key in ("target_class", "metrics", "dimensions", "filters", "join_classes", "having", "order_by")
            if arguments.get(key) not in (None, "", [], {})
        }

    @staticmethod
    def _query_scope_classes(arguments: dict, target_class: str, class_id: str = "") -> list[str]:
        raw_join_classes = arguments.get("join_classes") or []
        if isinstance(raw_join_classes, str):
            join_classes = [raw_join_classes]
        else:
            join_classes = list(raw_join_classes) if isinstance(raw_join_classes, list) else []
        return EntityDisambiguatorAgent._dedupe_nonempty([target_class, class_id, *join_classes])

    def _normalize_quarter_filter(self, item: dict, target_class: str, engine, scenario_id: str = "") -> dict:
        field = str(item.get("field", "")).strip()
        if not self._is_apmonth_field(field, target_class, engine) or not self._has_quarter_field(target_class, engine):
            return item

        quarter_value = self._quarter_value_from_filter(item)
        if not quarter_value:
            return item

        fixed = {
            **item,
            "field": self._quarter_filter_field(target_class, engine),
            "operator": "=",
            "value": quarter_value,
        }
        fixed["_intercepted"] = True
        logger.warning(
            "AP month filter normalized to quarter filter: scenario_id=%s original_filter=%s fixed_filter=%s",
            scenario_id,
            json.dumps(item, ensure_ascii=False, default=str)[:500],
            json.dumps(fixed, ensure_ascii=False, default=str)[:500],
        )
        return fixed

    @staticmethod
    def _is_apmonth_field(field: str, target_class: str, engine) -> bool:
        if field == "apmonth":
            return True
        if not target_class:
            return False
        field_map = engine.get_field_map(target_class)
        return field_map.get(field, field) == "apmonth"

    @staticmethod
    def _has_quarter_field(target_class: str, engine) -> bool:
        if not target_class:
            return False
        field_map = engine.get_field_map(target_class)
        field_types = engine.get_field_types(target_class)
        return "quarter_cd" in field_map.values() or "quarter_cd" in field_types

    @staticmethod
    def _quarter_filter_field(target_class: str, engine) -> str:
        for logical, physical in engine.get_field_map(target_class).items():
            if physical == "quarter_cd" and re.search(r"[\u4e00-\u9fff]", str(logical or "")):
                return logical
        return "quarter_cd"

    @classmethod
    def _quarter_value_from_filter(cls, item: dict) -> str:
        operator = str(item.get("operator") or "=").upper()
        value = item.get("value")
        if isinstance(value, str):
            return cls._normalize_quarter_value(value)
        if operator == "IN" and isinstance(value, list):
            return cls._quarter_from_ap_month_values(value)
        if operator == "BETWEEN" and isinstance(value, list) and len(value) == 2:
            start, end = [str(item).strip().upper() for item in value]
            if valid_ap_month(start) and valid_ap_month(end):
                quarter_months = current_quarter_ap_months(start)
                if [start, end] == [quarter_months[0], quarter_months[-1]]:
                    return cls._quarter_from_ap_month_values(quarter_months)
        return ""

    @staticmethod
    def _normalize_quarter_value(value: str) -> str:
        normalized = value.strip().upper().replace(" ", "")
        if QUARTER_VALUE_PATTERN.fullmatch(normalized):
            return normalized
        if BARE_QUARTER_VALUE_PATTERN.fullmatch(normalized):
            return f"{datetime.now().year}{normalized}"
        chinese_value = value.strip().replace(" ", "")
        match = CHINESE_QUARTER_VALUE_PATTERN.fullmatch(chinese_value)
        if not match:
            return ""
        quarter_map = {"一": "1", "二": "2", "三": "3", "四": "4"}
        year = match.group("year") or str(datetime.now().year)
        quarter = quarter_map.get(match.group("quarter"), match.group("quarter"))
        return f"{year}Q{quarter}"


    @staticmethod
    def _resolve_filter_class(
        engine,
        target_class: str,
        field: str,
        query_engine=None,
        allowed_classes: list[str] | None = None,
    ) -> str:
        scoped_classes = EntityDisambiguatorAgent._dedupe_nonempty(allowed_classes or [target_class])
        for candidate_class in scoped_classes:
            try:
                if (
                    query_engine
                    and hasattr(query_engine, "field_available_in_class")
                    and query_engine.field_available_in_class(candidate_class, field)
                ):
                    return candidate_class
                field_map = engine.get_field_map(candidate_class)
                field_types = engine.get_field_types(candidate_class)
                if field in field_map or field in field_types or field in field_map.values():
                    return candidate_class
            except Exception:
                continue
        try:
            resolved = engine.find_class_by_field(field)
        except Exception:
            resolved = ""
        return (
            resolved if resolved in scoped_classes else (target_class or (scoped_classes[0] if scoped_classes else ""))
        )

    @classmethod
    def _resolve_filter_field_type(
        cls,
        engine,
        target_class: str,
        field: str,
        query_engine=None,
        allowed_classes: list[str] | None = None,
    ) -> tuple[str, str]:
        class_id = cls._resolve_filter_class(engine, target_class, field, query_engine, allowed_classes)
        field_type = engine.get_field_type(class_id, field) if class_id else "text"
        if field_type == "text":
            try:
                resolved_class = engine.find_class_by_field(field)
            except Exception:
                resolved_class = ""
            if resolved_class and resolved_class != class_id:
                resolved_type = engine.get_field_type(resolved_class, field)
                if resolved_type != "text":
                    return resolved_class, resolved_type
        return class_id, field_type

    @staticmethod
    def _infer_target_class(arguments: dict, engine) -> str:
        class_votes = Counter()
        for metric in arguments.get("metrics") or []:
            metric_info = engine.get_metric_info(metric)
            if metric_info:
                for class_id in metric_target_classes(metric_info):
                    class_votes[class_id] += 3
                continue
            class_id = engine.find_class_by_field(metric)
            if class_id:
                class_votes[class_id] += 2
        for field in arguments.get("dimensions") or []:
            class_id = engine.find_class_by_field(field)
            if class_id:
                class_votes[class_id] += 2
        for item in [*(arguments.get("filters") or []), *(arguments.get("having") or [])]:
            if not isinstance(item, dict):
                continue
            field = item.get("field", "")
            metric_info = engine.get_metric_info(field)
            if metric_info:
                for class_id in metric_target_classes(metric_info):
                    class_votes[class_id] += 1
                continue
            class_id = engine.find_class_by_field(field)
            if class_id:
                class_votes[class_id] += 1
        return class_votes.most_common(1)[0][0] if class_votes else ""

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

    async def _disambiguate_text_filter(
        self,
        query_engine,
        item: dict,
        value,
        target_class: str,
        class_id: str,
        allowed_classes: list[str],
        query_selection: dict | None = None,
        scenario_id: str = "",
    ) -> dict:
        field = str(item.get("field") or "")
        if not isinstance(value, str) or not value.strip():
            return {**item, "value": value}

        sample_match = await self._select_filter_column_from_samples(
            query_engine,
            field=field,
            value=value,
            allowed_classes=allowed_classes,
            query_selection=query_selection,
            scenario_id=scenario_id,
        )
        if sample_match:
            selected_class, selected_field, column_confidence = sample_match
            try:
                distinct_result = query_engine.get_field_distinct_values(selected_class, selected_field, limit=200)
            except Exception as exc:
                logger.info(
                    "Text filter distinct values unavailable after sample selection: scenario_id=%s selected_class=%s "
                    "selected_field=%s error=%s",
                    scenario_id,
                    selected_class,
                    selected_field,
                    str(exc),
                )
                distinct_result = {}
            candidates = distinct_result.get("matched_values") or distinct_result.get("values", [])
            logger.info(
                "Text filter sample column selected: scenario_id=%s original_field=%s selected_class=%s "
                "selected_field=%s column_confidence=%.3f distinct_count=%d",
                scenario_id,
                field,
                selected_class,
                selected_field,
                column_confidence,
                len(candidates),
            )
            best_match, score, _ = await self._match_value(
                value,
                candidates,
                field_name=selected_field,
                class_id=selected_class,
                selection_context=query_selection,
            )
            fixed = {**item, "field": selected_field, "value": best_match if score >= 0.75 and best_match else value}
            fixed["_class_id"] = selected_class
            if fixed["field"] != field or fixed["value"] != value:
                fixed["_intercepted"] = True
            logger.info(
                "Text filter disambiguated by sample: scenario_id=%s original_field=%s original_value=%s "
                "fixed_field=%s fixed_value=%s value_score=%.3f",
                scenario_id,
                field,
                value,
                fixed["field"],
                fixed["value"],
                score,
            )
            return fixed

        aligned = await self._align_text_filter_value(
            query_engine,
            class_id,
            field,
            value,
            allowed_classes=allowed_classes,
            selection_context=query_selection,
            scenario_id=scenario_id,
        )
        fixed = {**item, "value": aligned}
        if aligned != value:
            fixed["_intercepted"] = True
        return fixed

    async def _select_filter_column_from_samples(
        self,
        query_engine,
        field: str,
        value: str,
        allowed_classes: list[str],
        query_selection: dict | None = None,
        scenario_id: str = "",
    ) -> tuple[str, str, float] | None:
        if not hasattr(query_engine, "get_class_sample"):
            return None
        samples = self._load_filter_class_samples(query_engine, allowed_classes, field, scenario_id)
        if not samples:
            return None
        selected_class, selected_field, confidence = await self._llm_select_filter_column(
            field=field,
            value=value,
            query_selection=query_selection,
            samples=samples,
            scenario_id=scenario_id,
        )
        valid_columns = {(sample["class_id"], column) for sample in samples for column in sample.get("columns", [])}
        if (selected_class, selected_field) not in valid_columns or confidence < 0.65:
            logger.info(
                "Text filter sample column not selected: scenario_id=%s field=%s value=%s selected_class=%s "
                "selected_field=%s confidence=%.3f",
                scenario_id,
                field,
                value,
                selected_class,
                selected_field,
                confidence,
            )
            return None
        return selected_class, selected_field, confidence

    def _load_filter_class_samples(
        self,
        query_engine,
        allowed_classes: list[str],
        field: str,
        scenario_id: str = "",
    ) -> list[dict]:
        samples = []
        for candidate_class in self._dedupe_nonempty(allowed_classes):
            try:
                sample_result = query_engine.get_class_sample(candidate_class, limit=5)
            except Exception as exc:
                logger.info(
                    "Text filter class sample unavailable: scenario_id=%s class=%s field=%s error=%s",
                    scenario_id,
                    candidate_class,
                    field,
                    str(exc),
                )
                continue
            rows = sample_result.get("data") or sample_result.get("rows") or []
            table_rows = [row for row in rows if isinstance(row, dict)]
            columns = []
            for row in table_rows[:5]:
                for column in row:
                    if column not in columns:
                        columns.append(str(column))
            if not columns:
                continue
            samples.append({"class_id": candidate_class, "columns": columns, "rows": table_rows[:3]})
            logger.info(
                "Text filter class sample loaded: scenario_id=%s class=%s field=%s columns=%s row_count=%d",
                scenario_id,
                candidate_class,
                field,
                json.dumps(columns, ensure_ascii=False),
                len(table_rows),
            )
        return samples

    async def _llm_select_filter_column(
        self,
        field: str,
        value: str,
        query_selection: dict | None,
        samples: list[dict],
        scenario_id: str = "",
    ) -> tuple[str, str, float]:
        try:

            logger.info(
                "Text filter sample column selection started: scenario_id=%s field=%s value=%s sample_classes=%s",
                scenario_id,
                field,
                value,
                json.dumps([sample.get("class_id") for sample in samples], ensure_ascii=False),
            )
            client = get_async_client()
            response = await client.chat.completions.create(
                model=get_model_name(),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是数据库过滤条件列复核器。基于当前模型已选择的查询参数、原始过滤字段和值，"
                            "以及各 class 样本数据，"
                            "重新判断并选择最应该承载该过滤值的列。不要根据用户原始问题重新推断查询意图。"
                            "只能从 samples 中给出的 class_id 和 columns 原样选择；"
                            "不确定时输出空字符串。只输出 JSON："
                            '{"class_id": string, "field": string, "confidence": number}。'
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "query_selection": query_selection or {},
                                "original_field": field,
                                "filter_value": value,
                                "samples": samples,
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    },
                ],
                temperature=0,
                max_tokens=200,
            )
            payload = self._parse_llm_json(response.choices[0].message.content or "{}")
            return (
                str(payload.get("class_id") or ""),
                str(payload.get("field") or ""),
                float(payload.get("confidence") or 0),
            )
        except Exception as exc:
            logger.warning(
                "Text filter sample column selection failed: scenario_id=%s field=%s value=%s error=%s",
                scenario_id,
                field,
                value,
                str(exc),
            )
            return "", "", 0.0

    async def _align_text_filter_value(
        self,
        query_engine,
        class_id: str,
        field: str,
        value,
        allowed_classes: list[str],
        selection_context: dict | None = None,
        scenario_id: str = "",
    ):
        if isinstance(value, list):
            aligned_items = []
            changed = False
            for item in value:
                aligned = await self._align_text_filter_value(
                    query_engine,
                    class_id,
                    field,
                    item,
                    allowed_classes=allowed_classes,
                    selection_context=selection_context,
                    scenario_id=scenario_id,
                )
                aligned_items.append(aligned)
                changed = changed or aligned != item
            return aligned_items if changed else value
        if not isinstance(value, str) or not value.strip():
            return value
        try:
            lookup_value = value.strip().strip("%")
            candidate_class, candidates = self._value_candidates_for_filter(
                query_engine,
                class_id,
                field,
                lookup_value,
                allowed_classes=allowed_classes,
                scenario_id=scenario_id,
            )
            best_match, score, _ = await self._match_value(
                value,
                candidates,
                field_name=field,
                class_id=candidate_class,
                selection_context=selection_context,
            )
            if best_match and score >= 0.75:
                logger.info(
                    "Text filter value aligned: scenario_id=%s class=%s field=%s original=%s aligned=%s score=%.3f",
                    scenario_id,
                    candidate_class,
                    field,
                    value,
                    best_match,
                    score,
                )
                return best_match
        except Exception as exc:
            logger.error("Error: %s(field = %s, value = %s)", exc, field, value)
            return value
        return value

    def _value_candidates_for_filter(
        self,
        query_engine,
        class_id: str,
        field: str,
        lookup_value: str,
        allowed_classes: list[str],
        scenario_id: str = "",
    ) -> tuple[str, list[str]]:
        candidate_classes = self._dedupe_nonempty([class_id, *allowed_classes])
        for candidate_class in candidate_classes:
            values = self._get_field_values(candidate_class, field, query_engine, lookup_value, {})
            logger.info(
                "Text filter value candidates loaded: scenario_id=%s class=%s field=%s value=%s count=%d",
                scenario_id,
                candidate_class,
                field,
                lookup_value,
                len(values),
            )
            if values:
                return candidate_class, values
        return class_id, []

    @staticmethod
    def _dedupe_nonempty(items: list[str]) -> list[str]:
        seen = set()
        result = []
        for item in items:
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    @staticmethod
    def _quarter_from_ap_month_values(values: list) -> str:
        months = [str(value).strip().upper() for value in values]
        if len(months) != 3 or any(not valid_ap_month(month) for month in months):
            return ""
        quarter = ap_month_to_quarter(months[0])
        expected_months = current_quarter_ap_months(months[0])
        if sorted(months) == sorted(expected_months) and all(ap_month_to_quarter(month) == quarter for month in months):
            return quarter
        return ""