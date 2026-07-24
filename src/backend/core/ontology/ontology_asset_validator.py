import json
import math
import re
from pathlib import Path
from typing import Any, Callable


class OntologyAssetValidator:
    def __init__(self, ensure_concept_coverage: Callable[[list[dict], list[dict]], list[dict]] | None = None):
        self.ensure_concept_coverage = ensure_concept_coverage

    def validate_schema_assets(self, schema: dict, summaries: list[dict]) -> dict:
        summary_index = self._build_summary_index(summaries)
        valid_classes, class_fields, class_metric_fields, class_ids = self._validate_classes(schema.get("classes", []), summary_index)
        valid_relationships = self._validate_relationships(schema.get("relationships", []), class_fields, class_ids)
        valid_dimension_groups = self._validate_dimension_groups(
            schema.get("dimension_groups", []), class_metric_fields, class_ids
        )
        valid_group_ids = {group["id"] for group in valid_dimension_groups}
        valid_metrics = self._validate_metrics(schema.get("metrics", []), class_metric_fields, class_ids, valid_group_ids)
        valid_concepts = self._validate_concepts(schema.get("concepts", []), class_ids)
        if self.ensure_concept_coverage:
            valid_concepts = self.ensure_concept_coverage(valid_classes, valid_concepts)
            valid_concepts = self._validate_concepts(valid_concepts, class_ids)

        return {
            **schema,
            "classes": valid_classes,
            "relationships": valid_relationships,
            "dimension_groups": valid_dimension_groups,
            "metrics": valid_metrics,
            "concepts": valid_concepts,
        }

    def _validate_classes(self, classes: list[dict], summary_index: dict[str, dict]) -> tuple[list[dict], dict[str, set[str]], dict[str, set[str]], set[str]]:
        valid_classes = []
        class_fields = {}
        class_metric_fields = {}
        class_ids = set()

        for cls in classes:
            if not isinstance(cls, dict):
                continue
            cid = str(cls.get("id", "")).strip()
            source = self._resolve_class_source(cls)
            summary = summary_index.get(source) or summary_index.get(source.lower())
            if not cid or not summary:
                self._log_asset_drop("class", cid or "<empty>", f"无法匹配物理数据源: {source or '<empty>'}")
                continue

            physical_columns = {str(col).strip() for col in summary.get("columns", []) if str(col).strip()}
            if not physical_columns:
                self._log_asset_drop("class", cid, f"物理数据源无字段: {source}")
                continue

            fields = []
            seen = set()
            for field in cls.get("fields", []):
                if not isinstance(field, dict):
                    continue
                is_legacy_field = bool(field.get("physical_name"))
                physical_name = str(
                    field.get("physical_name") if is_legacy_field else field.get("name") or ""
                ).strip()
                if not physical_name:
                    continue
                if physical_name not in physical_columns:
                    self._log_asset_drop("field", f"{cid}.{physical_name}", f"字段不属于物理数据源 {source}")
                    continue
                if physical_name in seen:
                    continue
                seen.add(physical_name)
                logical_name = str(
                    field.get("name") if is_legacy_field else field.get("name_cn") or physical_name
                ).strip()
                fields.append({**field, "name_cn": logical_name, "name": physical_name})

            if not fields:
                self._log_asset_drop("class", cid, "没有任何有效物理字段")
                continue

            primary_key = self._filter_field_list(cls.get("primary_key", ""), seen)
            if cls.get("primary_key") and not primary_key:
                self._log_asset_drop("primary_key", cid, f"主键字段不存在: {cls.get('primary_key')}")
            cls["fields"] = fields
            cls["primary_key"] = ",".join(primary_key)
            class_fields[cid] = seen
            class_metric_fields[cid] = seen | {
                str(field.get("name_cn") or field.get("name") or "").strip()
                for field in fields
                if str(field.get("name") or "").strip()
            }
            class_ids.add(cid)
            valid_classes.append(cls)

        return valid_classes, class_fields, class_metric_fields, class_ids

    def _validate_relationships(self, relationships: list[dict], class_fields: dict[str, set[str]], class_ids: set[str]) -> list[dict]:
        valid = []
        for rel in relationships:
            if not isinstance(rel, dict):
                continue
            source = str(rel.get("source", "")).strip()
            target = str(rel.get("target", "")).strip()
            if source not in class_ids or target not in class_ids:
                self._log_asset_drop("relationship", f"{source}->{target}", "关联 Class 不存在或已被剔除")
                continue

            source_keys = self._filter_field_list(rel.get("source_key", ""), class_fields.get(source, set()))
            target_keys = self._filter_field_list(rel.get("target_key", ""), class_fields.get(target, set()))
            if not source_keys or not target_keys:
                self._log_asset_drop("relationship", f"{source}->{target}", "关联键不存在于对应 Class 的物理字段")
                continue
            if len(source_keys) != len(target_keys):
                self._log_asset_drop("relationship", f"{source}->{target}", "源/目标关联键数量不一致")
                continue

            rel["source_key"] = ",".join(source_keys)
            rel["target_key"] = ",".join(target_keys)
            valid.append(rel)
        return valid

    def _validate_dimension_groups(self, groups: list[dict], class_fields: dict[str, set[str]], class_ids: set[str]) -> list[dict]:
        valid = []
        seen_ids = set()
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_id = str(group.get("id", "")).strip()
            group_type = str(group.get("group_type", "categorical")).strip()
            if not group_id or group_id in seen_ids or group_type not in {"time", "categorical", "hierarchy"}:
                self._log_asset_drop("dimension_group", group_id or "<empty>", "ID 重复/为空或类型无效")
                continue
            options = group.get("options", [])
            if not isinstance(options, list) or not options:
                self._log_asset_drop("dimension_group", group_id, "缺少业务选项")
                continue
            option_values = []
            normalized_options = []
            for option in options:
                if not isinstance(option, dict):
                    continue
                value = str(option.get("value", "")).strip()
                label = str(option.get("label", "")).strip()
                if not value or not label or value in option_values:
                    continue
                option_values.append(value)
                normalized_options.append({
                    "value": value,
                    "label": label,
                    "aliases": self._parse_json_list(option.get("aliases", [])),
                    "is_default": bool(option.get("is_default", False)),
                    "sort_order": int(option.get("sort_order", len(normalized_options))),
                    "status": "approved",
                })
            if not normalized_options:
                self._log_asset_drop("dimension_group", group_id, "没有有效业务选项")
                continue
            default_option = str(group.get("default_option", "")).strip()
            if default_option and default_option not in option_values:
                default_option = ""
            if not default_option:
                default_option = next((option["value"] for option in normalized_options if option["is_default"]), normalized_options[0]["value"])
            normalized_mappings = []
            for mapping in group.get("field_mappings", []):
                if not isinstance(mapping, dict):
                    continue
                option_value = str(mapping.get("option_value", "")).strip()
                class_id = str(mapping.get("class_id", "")).strip()
                field_name = str(mapping.get("field_name", "")).strip()
                if option_value not in option_values or class_id not in class_ids or field_name not in class_fields.get(class_id, set()):
                    self._log_asset_drop("dimension_mapping", f"{group_id}.{option_value}", "选项、Class 或字段无效")
                    continue
                normalized_mappings.append({
                    "option_value": option_value,
                    "class_id": class_id,
                    "field_name": field_name,
                    "display_name": str(mapping.get("display_name", "")).strip(),
                    "priority": int(mapping.get("priority", len(normalized_mappings))),
                })
            if not normalized_mappings:
                self._log_asset_drop("dimension_group", group_id, "没有有效字段映射")
                continue
            seen_ids.add(group_id)
            valid.append({
                "id": group_id,
                "name": str(group.get("name") or group_id).strip(),
                "description": str(group.get("description", "")).strip(),
                "group_type": group_type,
                "concept_id": "",
                "is_required": bool(group.get("is_required", False)),
                "default_option": default_option,
                "clarification_policy": str(group.get("clarification_policy") or "ask_when_ambiguous"),
                "status": "pending",
                "options": normalized_options,
                "field_mappings": normalized_mappings,
            })
        return valid

    def _validate_metrics(self, metrics: list[dict], class_fields: dict[str, set[str]], class_ids: set[str], valid_group_ids: set[str]) -> list[dict]:
        valid = []
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            metric_id = str(metric.get("id", "")).strip() or "<empty>"
            target_class = str(metric.get("target_class", "")).strip()
            if target_class not in class_ids:
                self._log_asset_drop("metric", metric_id, f"target_class 不存在或已被剔除: {target_class or '<empty>'}")
                continue

            fields = class_fields.get(target_class, set())
            definition = metric.get("definition", {})
            if isinstance(definition, str):
                try:
                    definition = json.loads(definition or "{}")
                except json.JSONDecodeError:
                    definition = {}
            version = definition.get("version") if isinstance(definition, dict) else None
            if version not in {1, 2}:
                self._log_asset_drop("metric", metric_id, "缺少有效的结构化 definition")
                continue
            if str(definition.get("anchor_class", "")).strip() != target_class:
                self._log_asset_drop("metric", metric_id, "definition.anchor_class 与 target_class 不一致")
                continue
            if version == 1:
                operator = str(definition.get("expression_operator", "")).upper()
                input_groups = [definition.get("inputs", [])]
                if operator not in {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "CONCAT"}:
                    self._log_asset_drop("metric", metric_id, "definition.expression_operator 不支持")
                    continue
                try:
                    offset = float(definition.get("offset") or 0)
                except (TypeError, ValueError):
                    offset = float("nan")
                if not math.isfinite(offset) or (operator == "CONCAT" and offset):
                    self._log_asset_drop("metric", metric_id, "definition 计算结果调整值无效")
                    continue
            else:
                outputs = definition.get("outputs", [])
                names = [str(output.get("output_name", "")).strip() for output in outputs if isinstance(output, dict)] if isinstance(outputs, list) else []
                if not names or len(names) != len(outputs) or len(set(names)) != len(names):
                    self._log_asset_drop("metric", metric_id, "并列输出名称为空或重复")
                    continue
                input_groups = [output.get("inputs", []) for output in outputs]
                if any(
                    str(output.get("expression_operator", "")).upper() not in {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE"}
                    or not isinstance(output.get("inputs"), list)
                    or not output.get("inputs")
                    or (
                        str(output.get("expression_operator", "")).upper() == "DIVIDE"
                        and len(output.get("inputs", [])) != 2
                    )
                    for output in outputs
                ):
                    self._log_asset_drop("metric", metric_id, "并列输出表达式无效")
                    continue
                try:
                    valid_offsets = all(
                        not isinstance(output.get("offset"), bool)
                        and math.isfinite(float(output.get("offset") or 0))
                        for output in outputs
                    )
                except (TypeError, ValueError):
                    valid_offsets = False
                if not valid_offsets:
                    self._log_asset_drop("metric", metric_id, "并列输出计算结果调整值无效")
                    continue
            input_error = ""
            for inputs in input_groups:
                for item in inputs:
                    if not isinstance(item, dict):
                        input_error = "definition 组成项格式无效"
                        break
                    source_class = str(item.get("class_id", "")).strip()
                    source_shape = str(item.get("source_shape", "wide")).lower().strip()
                    source_field = str(item.get("field", "")).strip()
                    aggregation = str(item.get("aggregation", "")).upper().strip()
                    filters = item.get("filters", [])
                    if source_class not in class_ids or source_field not in class_fields.get(source_class, set()):
                        input_error = "definition 组成项引用了无效 Class 或字段"
                        break
                    if version == 1 and not str(item.get("output_name", "")).strip():
                        input_error = "definition 组成项缺少 output_name"
                        break
                    if source_shape not in {"wide", "long"}:
                        input_error = "definition 组成项的 source_shape 不支持"
                        break
                    if aggregation not in {"SUM", "AVG", "MIN", "MAX", "COUNT", "COUNT_DISTINCT"}:
                        input_error = "definition 组成项的 aggregation 不支持"
                        break
                    if not isinstance(filters, list) or (source_shape == "long" and not filters):
                        input_error = "窄表 definition 组成项必须包含固定条件"
                        break
                    if not all(
                        isinstance(filter_item, dict)
                        and str(filter_item.get("field", "")).strip() in class_fields.get(source_class, set())
                        and str(filter_item.get("operator", "")).upper().strip() in {"=", "!=", "IN", "NOT IN", "IS NULL", "IS NOT NULL"}
                        and (
                            str(filter_item.get("operator", "")).upper().strip() in {"IS NULL", "IS NOT NULL"}
                            or filter_item.get("value") not in (None, "", [])
                        )
                        for filter_item in filters
                    ):
                        input_error = "definition 组成项包含无效固定条件"
                        break
                if input_error:
                    break
            if input_error:
                self._log_asset_drop("metric", metric_id, input_error)
                continue

            dimensions = self._parse_json_list(metric.get("dimensions", []))
            required_dimensions = self._parse_json_list(metric.get("required_dimensions", []))
            valid_dimensions = [field for field in dimensions if field in fields]
            invalid_dimensions = [field for field in dimensions if field not in fields]
            if invalid_dimensions:
                self._log_asset_drop("metric_dimension", metric_id, f"剔除无效维度字段: {invalid_dimensions}")

            invalid_required = [field for field in required_dimensions if field not in valid_dimensions]
            if invalid_required:
                self._log_asset_drop("metric", metric_id, f"required_dimensions 包含无效字段: {invalid_required}")
                continue

            metric["dimensions"] = json.dumps(valid_dimensions, ensure_ascii=False)
            metric["required_dimensions"] = json.dumps(required_dimensions, ensure_ascii=False)
            metric["dimension_group_ids"] = [
                group_id for group_id in self._parse_json_list(metric.get("dimension_group_ids", []))
                if group_id in valid_group_ids
            ]
            valid.append(metric)
        return valid

    def _validate_concepts(self, concepts: list[dict], class_ids: set[str]) -> list[dict]:
        valid = []
        seen_ids = set()
        for concept in concepts:
            if not isinstance(concept, dict):
                continue
            concept_id = str(concept.get("id", "")).strip()
            related_class = str(concept.get("related_class", "")).strip()
            if not concept_id or concept_id in seen_ids:
                self._log_asset_drop("concept", concept_id or "<empty>", "概念 ID 为空或重复")
                continue
            if related_class and related_class not in class_ids:
                self._log_asset_drop("concept", concept_id, f"related_class 不存在或已被剔除: {related_class}")
                continue
            seen_ids.add(concept_id)
            valid.append(concept)

        valid_ids = {concept.get("id") for concept in valid}
        cleaned = []
        for concept in valid:
            concept_id = str(concept.get("id", "")).strip()
            parent_id = str(concept.get("parent_id", "")).strip()
            if parent_id and parent_id not in valid_ids:
                self._log_asset_drop("concept", concept_id, f"parent_id 不存在或已被剔除: {parent_id}")
                continue
            cleaned.append(concept)
        return cleaned

    def _build_summary_index(self, summaries: list[dict]) -> dict[str, dict]:
        summary_index = {}
        for summary in summaries:
            file_name = str(summary.get("file", "")).strip()
            if not file_name:
                continue
            keys = {file_name, file_name.lower(), Path(file_name).name, Path(file_name).name.lower()}
            if file_name.endswith(".csv"):
                stem = file_name[:-4]
                keys.update({stem, stem.lower(), Path(stem).name, Path(stem).name.lower()})
            for key in keys:
                if key:
                    summary_index[key] = summary
        return summary_index

    def _resolve_class_source(self, cls: dict) -> str:
        for key in ("_source_origin", "table_name", "table_name"):
            value = str(cls.get(key, "")).strip()
            if value:
                return value
        return ""

    def _parse_json_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            items = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                items = []
            else:
                try:
                    parsed = json.loads(stripped)
                    items = parsed if isinstance(parsed, list) else [parsed]
                except json.JSONDecodeError:
                    items = re.split(r"[,，]", stripped)
        else:
            items = []
        return [str(item).strip() for item in items if str(item).strip()]

    def _filter_field_list(self, value: Any, valid_fields: set[str]) -> list[str]:
        return [field for field in self._parse_json_list(value) if field in valid_fields]

    def _extract_formula_fields(self, formula: Any) -> list[str]:
        text_formula = str(formula or "")
        if not text_formula:
            return []

        text_formula = self._strip_formula_output_aliases(text_formula)

        text_formula = re.sub(r"'[^']*'", " ", text_formula)
        quoted_fields = []
        for pattern in (r'"([^"]+)"', r"`([^`]+)`", r"\[([^\]]+)\]"):
            quoted_fields.extend(match.strip() for match in re.findall(pattern, text_formula) if match.strip())
            text_formula = re.sub(pattern, " ", text_formula)

        function_names = {match.upper() for match in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", text_formula)}
        sql_words = {
            "ABS", "AVG", "CASE", "CAST", "COALESCE", "COUNT", "DATE", "DAY", "DISTINCT", "ELSE", "END", "FALSE",
            "FLOOR", "IF", "IN", "MAX", "MIN", "MONTH", "NULL", "NULLIF", "ROUND", "SUM", "THEN", "TRUE", "WHEN", "YEAR"
        }
        bare_fields = []
        for token in re.findall(r"[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*", text_formula):
            upper_token = token.upper()
            if upper_token in sql_words or upper_token in function_names:
                continue
            bare_fields.append(token)

        fields = []
        for field in quoted_fields + bare_fields:
            if field not in fields:
                fields.append(field)
        return fields

    def _split_top_level_commas(self, text: str) -> list[str]:
        parts = []
        start = 0
        depth = 0
        quote_char = ""
        index = 0
        while index < len(text):
            ch = text[index]
            if quote_char:
                if ch == quote_char:
                    if index + 1 < len(text) and text[index + 1] == quote_char:
                        index += 1
                    else:
                        quote_char = ""
                index += 1
                continue

            if ch in {"'", '"', "`"}:
                quote_char = ch
            elif ch == "(":
                depth += 1
            elif ch == ")" and depth > 0:
                depth -= 1
            elif ch == "," and depth == 0:
                part = text[start:index].strip()
                if part:
                    parts.append(part)
                start = index + 1
            index += 1

        tail = text[start:].strip()
        if tail:
            parts.append(tail)
        return parts

    def _strip_formula_output_aliases(self, formula: str) -> str:
        parts = []
        for part in self._split_top_level_commas(formula):
            alias_match = re.match(r"(?is)^(.*?)\s+AS\s+[A-Za-z_][\w]*\s*$", part)
            if not alias_match:
                alias_match = re.match(r"(?is)^(.*\))\s+[A-Za-z_][\w]*\s*$", part)
            parts.append(alias_match.group(1).strip() if alias_match else part)
        return ", ".join(parts)

    def _log_asset_drop(self, asset_type: str, asset_id: str, reason: str) -> None:
        print(f"[Ontology Validation] drop {asset_type} {asset_id}: {reason}")


def validate_schema_assets(
    schema: dict,
    summaries: list[dict],
    ensure_concept_coverage: Callable[[list[dict], list[dict]], list[dict]] | None = None,
) -> dict:
    return OntologyAssetValidator(ensure_concept_coverage).validate_schema_assets(schema, summaries)
