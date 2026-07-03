import json
import re
from pathlib import Path
from typing import Any, Callable


class OntologyAssetValidator:
    def __init__(self, ensure_concept_coverage: Callable[[list[dict], list[dict]], list[dict]] | None = None):
        self.ensure_concept_coverage = ensure_concept_coverage

    def validate_schema_assets(self, schema: dict, summaries: list[dict]) -> dict:
        summary_index = self._build_summary_index(summaries)
        valid_classes, class_fields, class_ids = self._validate_classes(schema.get("classes", []), summary_index)
        valid_relationships = self._validate_relationships(schema.get("relationships", []), class_fields, class_ids)
        valid_metrics = self._validate_metrics(schema.get("metrics", []), class_fields, class_ids)
        valid_concepts = self._validate_concepts(schema.get("concepts", []), class_ids)
        if self.ensure_concept_coverage:
            valid_concepts = self.ensure_concept_coverage(valid_classes, valid_concepts)
            valid_concepts = self._validate_concepts(valid_concepts, class_ids)

        return {
            **schema,
            "classes": valid_classes,
            "relationships": valid_relationships,
            "metrics": valid_metrics,
            "concepts": valid_concepts,
        }

    def _validate_classes(self, classes: list[dict], summary_index: dict[str, dict]) -> tuple[list[dict], dict[str, set[str]], set[str]]:
        valid_classes = []
        class_fields = {}
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
                physical_name = str(field.get("physical_name") or field.get("name") or "").strip()
                if not physical_name:
                    continue
                if physical_name not in physical_columns:
                    self._log_asset_drop("field", f"{cid}.{physical_name}", f"字段不属于物理数据源 {source}")
                    continue
                if physical_name in seen:
                    continue
                seen.add(physical_name)
                fields.append({**field, "physical_name": physical_name})

            if not fields:
                self._log_asset_drop("class", cid, "没有任何有效物理字段")
                continue

            primary_key = self._filter_field_list(cls.get("primary_key", ""), seen)
            if cls.get("primary_key") and not primary_key:
                self._log_asset_drop("primary_key", cid, f"主键字段不存在: {cls.get('primary_key')}")
            cls["fields"] = fields
            cls["primary_key"] = ",".join(primary_key)
            class_fields[cid] = seen
            class_ids.add(cid)
            valid_classes.append(cls)

        return valid_classes, class_fields, class_ids

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

    def _validate_metrics(self, metrics: list[dict], class_fields: dict[str, set[str]], class_ids: set[str]) -> list[dict]:
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
            formula_fields = self._extract_formula_fields(metric.get("formula", ""))
            invalid_formula_fields = [field for field in formula_fields if field not in fields]
            if invalid_formula_fields:
                self._log_asset_drop("metric", metric_id, f"公式字段不属于 {target_class}: {invalid_formula_fields}")
                continue

            dimensions = self._parse_json_list(metric.get("dimensions", []))
            required_dimensions = self._parse_json_list(metric.get("required_dimensions", []))
            valid_dimensions = [field for field in dimensions if field in fields]
            invalid_dimensions = [field for field in dimensions if field not in fields]
            if invalid_dimensions:
                self._log_asset_drop("metric_dimension", metric_id, f"剔除无效维度字段: {invalid_dimensions}")

            invalid_required = [field for field in required_dimensions if field not in fields]
            if invalid_required:
                self._log_asset_drop("metric", metric_id, f"required_dimensions 包含无效字段: {invalid_required}")
                continue

            metric["dimensions"] = json.dumps(valid_dimensions, ensure_ascii=False)
            metric["required_dimensions"] = json.dumps(required_dimensions, ensure_ascii=False)
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
        for key in ("_source_origin", "csv_file", "table_name"):
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
