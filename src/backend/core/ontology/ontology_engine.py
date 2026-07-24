"""
Ontology Engine v2 - 本体引擎
============================
核心升级：
  1. 支持 source_key / target_key 细化 JOIN 关联键（兼容旧版 join_key）
  2. 支持 field_types 字段类型声明，为 SQL 过滤条件类型安全提供依据
  3. 多跳 JOIN 路径推导（BFS），支持跨 2+ 表的关联查询
  4. 反向关系自动推导
  5. 字段归属校验：判断某字段属于哪个 class
"""

import json
from pathlib import Path
from typing import Optional
from collections import deque

from core.db.db import get_db


class OntologyEngine:
    def __init__(self, ontology_dir: str, data_dir: str):
        self.ontology_dir = Path(ontology_dir)
        self.data_dir = Path(data_dir)
        self.schema: dict = {}
        self.mapping: dict = {}
        self.classes: dict = {}
        self.relationships: list = []
        self.metrics: list = []
        self._load()

    def _load(self):
        """Load canonical ontology assets from the database.

        The JSON files remain an import/extraction fallback for scenarios that
        have not been persisted yet; runtime reads should not depend on them
        being regenerated after an admin-side change.
        """
        self.schema = {}
        self.mapping = {}
        self.classes = {}
        self.relationships = []
        self.metrics = []
        if self._load_from_database():
            self._filter_rejected_assets()
            return

        # schema_path = self.ontology_dir / "schema.json"
        # mapping_path = self.ontology_dir / "schema_mapping.json"
        # if schema_path.exists():
        #     with open(schema_path, "r", encoding="utf-8") as f:
        #         self.schema = json.load(f)
        # if mapping_path.exists():
        #     with open(mapping_path, "r", encoding="utf-8") as f:
        #         self.mapping = json.load(f)
        #         self.classes = self.mapping.get("classes", {})
        #         self.relationships = self._normalize_relationships(
        #             self.mapping.get("relationships", [])
        #         )
        # self._filter_rejected_assets()

    def _load_from_database(self) -> bool:
        """Build the in-memory schema/mapping model from scenario-scoped rows."""
        scenario_id = self.ontology_dir.parent.name
        if not scenario_id:
            return False
        try:
            conn = get_db()
            try:
                class_rows = conn.execute(
                    "SELECT * FROM schema_classes WHERE scenario_id=?", (scenario_id,)
                ).fetchall()
                if not class_rows:
                    return False
                relationship_rows = conn.execute(
                    "SELECT * FROM schema_relationships WHERE scenario_id=?", (scenario_id,)
                ).fetchall()
                metric_rows = conn.execute(
                    "SELECT * FROM metrics WHERE scenario_id=?", (scenario_id,)
                ).fetchall()
                concept_rows = conn.execute(
                    "SELECT * FROM concepts WHERE scenario_id=?", (scenario_id,)
                ).fetchall()
                dimension_group_rows = conn.execute(
                    "SELECT * FROM dimension_groups WHERE scenario_id=? ORDER BY name", (scenario_id,)
                ).fetchall()
                dimension_option_rows = conn.execute(
                    "SELECT * FROM dimension_group_options WHERE scenario_id=? ORDER BY group_id, sort_order", (scenario_id,)
                ).fetchall()
                dimension_mapping_rows = conn.execute(
                    "SELECT * FROM dimension_field_mappings WHERE scenario_id=? ORDER BY group_id, priority, id", (scenario_id,)
                ).fetchall()
                metric_binding_rows = conn.execute(
                    "SELECT metric_id, group_id FROM metric_dimension_bindings WHERE scenario_id=? ORDER BY metric_id, group_id", (scenario_id,)
                ).fetchall()
                metric_concept_binding_rows = conn.execute(
                    """SELECT metric_id, concept_id, role, priority, is_primary, status
                       FROM metric_concept_bindings WHERE scenario_id=? ORDER BY metric_id, priority, concept_id""",
                    (scenario_id,),
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            # JSON fallback preserves standalone extraction/import workflows.
            return False

        classes = []
        mapping_classes = {}
        for row in class_rows:
            item = dict(row)
            fields = self._json_list(item.get("fields"))
            properties = self._json_list(item.get("properties"))
            normalized_fields = []
            field_map = {}
            field_types = {}
            for field in fields:
                if not isinstance(field, dict):
                    continue
                # New rows use {name_cn: logical, name: physical}; existing
                # rows remain {name: logical, physical_name: physical} until
                # the database migration has been run.
                is_legacy_field = bool(field.get("physical_name"))
                physical_name = str(
                    field.get("physical_name") if is_legacy_field else field.get("name")
                    or ""
                ).strip()
                logical_name = str(
                    field.get("name") if is_legacy_field else field.get("name_cn") or physical_name
                ).strip()
                if not logical_name or not physical_name:
                    continue
                normalized = {
                    **field,
                    "name_cn": logical_name,
                    "name": physical_name,
                    "type": str(field.get("type") or "text"),
                }
                normalized_fields.append(normalized)
                field_map[logical_name] = physical_name
                field_types[physical_name] = normalized["type"]
            if not properties:
                properties = [field["name_cn"] for field in normalized_fields]
            review_status = self._review_status(item)
            classes.append(
                {
                    "id": item.get("id", ""),
                    "name_cn": item.get("name_cn", ""),
                    "description": item.get("description", ""),
                    "properties": properties,
                    "primary_key": item.get("primary_key", ""),
                    "table_name": item.get("table_name", ""),
                    "fields": normalized_fields,
                    "is_reviewed": item.get("is_reviewed", 0),
                    "review_status": review_status,
                }
            )
            table_name = str(item.get("table_name") or "")
            mapping_classes[str(item.get("id") or "")] = {
                # `table_name` in schema_classes is the authoritative source
                # identifier. For CSV-backed classes it includes the .csv suffix;
                # retain it separately because SQLite uses the suffix-free name.
                "source_file": table_name if table_name.lower().endswith(".csv") else "",
                "table_name": table_name.removesuffix(".csv") if table_name else item.get("id", ""),
                "primary_key": item.get("primary_key", ""),
                "name_cn": item.get("name_cn", ""),
                "field_map": field_map,
                "field_types": field_types,
                "data_source": "csv" if table_name.endswith(".csv") else "database",
                "is_reviewed": item.get("is_reviewed", 0),
                "review_status": review_status,
            }

        relationships = []
        for row in relationship_rows:
            item = dict(row)
            source_key = str(item.get("source_key") or item.get("join_key") or "")
            target_key = str(item.get("target_key") or item.get("join_key") or "")
            relationships.append(
                {
                    "source": item.get("source", ""),
                    "target": item.get("target", ""),
                    "type": item.get("type", "relates_to"),
                    "source_key": source_key,
                    "target_key": target_key,
                    "join_key": item.get("join_key") or (source_key if source_key == target_key else ""),
                    "description": item.get("description", ""),
                    "is_reviewed": item.get("is_reviewed", 0),
                    "review_status": self._review_status(item),
                }
            )

        metric_group_ids: dict[str, list[str]] = {}
        for binding in metric_binding_rows:
            metric_group_ids.setdefault(str(binding["metric_id"]), []).append(str(binding["group_id"]))
        metric_concept_bindings: dict[str, list[dict]] = {}
        for binding in metric_concept_binding_rows:
            item = dict(binding)
            if str(item.get("status") or "pending") != "approved":
                continue
            metric_concept_bindings.setdefault(str(item["metric_id"]), []).append(
                {
                    "concept_id": str(item["concept_id"]),
                    "role": str(item.get("role") or "outcome"),
                    "priority": int(item.get("priority") or 0),
                    "is_primary": bool(item.get("is_primary")),
                }
            )

        metrics = []
        for row in metric_rows:
            item = dict(row)
            metrics.append(
                {
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "name_cn": item.get("name", ""),
                    "description": item.get("description", ""),
                    "category": item.get("category", ""),
                    "target_class": item.get("target_class", ""),
                    "definition": self._json_dict(item.get("definition")),
                    "dimensions": self._json_list(item.get("dimensions")),
                    "required_dimensions": self._json_list(item.get("required_dimensions")),
                    "dimension_group_ids": metric_group_ids.get(str(item.get("id") or ""), []),
                    "concept_bindings": metric_concept_bindings.get(str(item.get("id") or ""), []),
                    "chart_type": item.get("chart_type") or "bar",
                    "sort_order": item.get("sort_order") or 0,
                    "is_reviewed": item.get("is_reviewed", 0),
                    "review_status": self._review_status(item),
                }
            )

        options_by_group: dict[str, list[dict]] = {}
        for row in dimension_option_rows:
            option = dict(row)
            options_by_group.setdefault(str(option["group_id"]), []).append({
                "value": option["value"],
                "label": option["label"],
                "aliases": self._json_list(option.get("aliases")),
                "is_default": bool(option.get("is_default")),
                "sort_order": option.get("sort_order", 0),
                "status": option.get("status", "draft"),
            })
        mappings_by_group: dict[str, list[dict]] = {}
        for row in dimension_mapping_rows:
            mapping = dict(row)
            mappings_by_group.setdefault(str(mapping["group_id"]), []).append({
                "option_value": mapping["option_value"],
                "class_id": mapping["class_id"],
                "field_name": mapping["field_name"],
                "display_name": mapping.get("display_name", ""),
                "priority": mapping.get("priority", 0),
            })
        dimension_groups = [
            {
                "id": group["id"],
                "name": group["name"],
                "description": group.get("description", ""),
                "group_type": group.get("group_type", "categorical"),
                "concept_id": group.get("concept_id", ""),
                "is_required": bool(group.get("is_required")),
                "default_option": group.get("default_option", ""),
                "clarification_policy": group.get("clarification_policy", "ask_when_ambiguous"),
                "status": group.get("status", "draft"),
                "options": options_by_group.get(str(group["id"]), []),
                "field_mappings": mappings_by_group.get(str(group["id"]), []),
            }
            for group in map(dict, dimension_group_rows)
        ]

        self.schema = {
            "business_name": scenario_id,
            "classes": classes,
            "relationships": relationships,
            "concepts": [self._database_concept(dict(row)) for row in concept_rows],
            "dimension_groups": dimension_groups,
            "metrics": metrics,
        }
        self.mapping = {"classes": mapping_classes, "relationships": relationships}
        self.classes = mapping_classes
        self.relationships = self._normalize_relationships(relationships)
        self.metrics = metrics
        return True

    @staticmethod
    def _json_list(value) -> list:
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(value or "[]")
            return parsed if isinstance(parsed, list) else []
        except (TypeError, json.JSONDecodeError):
            return []

    @staticmethod
    def _json_dict(value) -> dict:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _database_concept(item: dict) -> dict:
        return {
            key: item.get(key, "")
            for key in (
                "id", "name", "description", "parent_id", "level", "concept_type",
                "related_class", "sort_order", "is_reviewed", "review_status",
            )
        }

    def _filter_rejected_assets(self) -> None:
        class_statuses = {
            str(schema_class.get("id") or ""): self._review_status(schema_class)
            for schema_class in self.schema.get("classes", [])
        }
        rejected_class_ids = {
            class_id for class_id, review_status in class_statuses.items() if review_status == "rejected"
        }
        self.classes = {
            class_id: class_info
            for class_id, class_info in self.classes.items()
            if class_id not in rejected_class_ids
        }
        self.relationships = [
            relationship
            for relationship in self.relationships
            if relationship.get("source") not in rejected_class_ids
            and relationship.get("target") not in rejected_class_ids
        ]
        self.metrics = [
            metric
            for metric in self.schema.get("metrics", [])
            if self._metric_is_available(metric, class_statuses)
        ]

    @staticmethod
    def _review_status(item: dict) -> str:
        """Normalize current and legacy review fields without depending on API modules."""
        review_status = str(item.get("review_status") or "").lower()
        is_reviewed = item.get("is_reviewed", 0)
        if review_status == "rejected" or is_reviewed == -1 or str(is_reviewed).lower() == "rejected":
            return "rejected"
        if review_status == "approved" or is_reviewed == 1 or str(is_reviewed).lower() in {"true", "yes", "y"}:
            return "approved"
        return "pending"

    @staticmethod
    def _metric_target_classes(metric: dict) -> list[str]:
        """Read the Metric anchor and structured input classes."""
        definition = metric.get("definition", {})
        if isinstance(definition, str):
            try:
                definition = json.loads(definition or "{}")
            except json.JSONDecodeError:
                definition = {}
        definition = definition if isinstance(definition, dict) else {}
        targets = [definition.get("anchor_class")]
        targets.extend(
            item.get("class_id")
            for item in definition.get("inputs", [])
            if isinstance(item, dict)
        )
        targets.append(metric.get("target_class") or metric.get("class_id"))
        return list(dict.fromkeys(str(target).strip() for target in targets if str(target or "").strip()))

    @classmethod
    def _metric_is_available(cls, metric: dict, class_statuses: dict[str, str]) -> bool:
        """Keep Metrics only when the Metric and every referenced source Class are not rejected."""
        if cls._review_status(metric) == "rejected":
            return False
        target_classes = cls._metric_target_classes(metric)
        return bool(target_classes) and all(
            class_statuses.get(class_id) != "rejected" for class_id in target_classes
        )

    # ──────────────────────────────────────────────────────────
    # 关系规范化：兼容旧版 join_key，升级为 source_key/target_key
    # ──────────────────────────────────────────────────────────
    def _normalize_relationships(self, raw_rels: list) -> list:
        """将旧版只有 join_key 的关系升级为 source_key/target_key 格式"""
        normalized = []
        for rel in raw_rels:
            r = dict(rel)
            # 如果已有 source_key/target_key，直接使用
            if "source_key" not in r:
                r["source_key"] = r.get("join_key", "")
            if "target_key" not in r:
                r["target_key"] = r.get("join_key", "")
            # 如果没有 type，默认为 "relates_to"
            if "type" not in r:
                r["type"] = "relates_to"
            normalized.append(r)
        return normalized

    # ──────────────────────────────────────────────────────────
    # 基础查询
    # ──────────────────────────────────────────────────────────
    def get_class_info(self, class_id: str) -> dict:
        """获取 class 的完整映射信息"""
        return self.classes.get(class_id, {})

    def get_table_name(self, class_id: str) -> str:
        """获取 class 对应的标准化物理表名。"""
        info = self.classes.get(class_id, {})
        table_name = str(info.get("table_name") or "").strip()
        if not table_name:
            raise ValueError(f"class {class_id} 未配置固定物理表映射，请检查 schema_mapping.json")
        return table_name

    def get_field_map(self, class_id: str) -> dict:
        """获取 class 的字段映射（逻辑名 -> 物理列名）"""
        info = self.classes.get(class_id, {})
        return info.get("field_map", {})

    def get_field_types(self, class_id: str) -> dict:
        """获取 class 的字段类型声明（逻辑名 -> 类型）"""
        info = self.classes.get(class_id, {})
        return info.get("field_types", {})

    def get_field_type(self, class_id: str, field_name: str) -> str:
        """获取单个字段的类型"""
        types = self.get_field_types(class_id)
        return types.get(field_name, "text")  # 默认为 text

    def map_field(self, class_id: str, field_name: str) -> str:
        """将逻辑字段名映射为物理列名，未找到则原样返回"""
        fm = self.get_field_map(class_id)
        return fm.get(field_name, field_name)

    def reverse_map_field(self, class_id: str, physical_col: str) -> str:
        """将物理列名反向映射为逻辑字段名"""
        fm = self.get_field_map(class_id)
        for logical, physical in fm.items():
            if physical == physical_col:
                return logical
        return physical_col

    def get_primary_key(self, class_id: str) -> str:
        """获取 class 的主键"""
        info = self.classes.get(class_id, {})
        return info.get("primary_key", "")

    def get_source_file(self, class_id: str) -> str:
        """Return the CSV source filename for an in-memory class, when present."""
        info = self.classes.get(class_id, {})
        source_file = str(info.get("source_file") or "").strip()
        if source_file:
            return source_file
        # Compatibility for legacy mappings that stored a CSV filename directly
        # under `table_name`.
        table_name = str(info.get("table_name") or "").strip()
        return table_name if table_name.lower().endswith(".csv") else ""

    def get_data_source(self, class_id: str) -> str:
        """获取 class 的数据源类型"""
        info = self.classes.get(class_id, {})
        return info.get("data_source", "csv")

    # ──────────────────────────────────────────────────────────
    # 指标元数据检索优化
    # ──────────────────────────────────────────────────────────
    def get_metric_info(self, metric_id_or_name: str) -> Optional[dict]:
        """根据指标 ID 或中文名查找 schema.json 中定义的完整指标元数据"""
        for m in self.metrics:
            if (
                m.get("id") == metric_id_or_name
                or m.get("name") == metric_id_or_name
                or m.get("name_cn") == metric_id_or_name
            ):
                return m
        return None

    def find_class_by_field(self, field_name: str) -> Optional[str]:
        """根据字段名查找所属的 class_id"""
        for class_id, info in self.classes.items():
            if field_name in info.get("field_map", {}):
                return class_id
        return None

    # ──────────────────────────────────────────────────────────
    # JOIN 路径推导
    # ──────────────────────────────────────────────────────────
    def get_join_path(self, source: str, target: str) -> list[dict]:
        """
        使用 BFS 推导两个 class 之间的 JOIN 路径。
        返回路径上的关系列表，每个关系包含 source, target, source_key, target_key。
        如果无法到达，返回空列表。
        """
        if source == target:
            return []

        # 构建邻接表
        adj: dict[str, list[dict]] = {}
        for rel in self.relationships:
            s, t = rel["source"], rel["target"]
            adj.setdefault(s, []).append(rel)
            # 反向关系
            reverse_rel = {
                "source": t,
                "target": s,
                "source_key": rel.get("target_key", rel.get("join_key", "")),
                "target_key": rel.get("source_key", rel.get("join_key", "")),
                "type": f"reverse_of_{rel.get('type', 'relates_to')}",
                "join_key": rel.get("join_key", ""),
            }
            adj.setdefault(t, []).append(reverse_rel)

        # BFS
        visited = {source}
        queue = deque()
        # 初始路径
        for rel in adj.get(source, []):
            if rel["target"] not in visited:
                new_path = [rel]
                if rel["target"] == target:
                    return new_path
                visited.add(rel["target"])
                queue.append((rel["target"], new_path))

        while queue:
            current, path = queue.popleft()
            for rel in adj.get(current, []):
                neighbor = rel["target"]
                if neighbor in visited:
                    continue
                new_path = path + [rel]
                if neighbor == target:
                    return new_path
                visited.add(neighbor)
                queue.append((neighbor, new_path))

        return []

    def get_direct_relationship(self, source: str, target: str) -> Optional[dict]:
        """获取两个 class 之间的直接关系"""
        for rel in self.relationships:
            if (rel["source"] == source and rel["target"] == target) or \
               (rel["source"] == target and rel["target"] == source):
                return rel
        return None

    def get_related_classes(self, class_id: str) -> list[str]:
        """获取与指定 class 有直接关系的所有 class"""
        related = set()
        for rel in self.relationships:
            if rel["source"] == class_id:
                related.add(rel["target"])
            elif rel["target"] == class_id:
                related.add(rel["source"])
        return list(related)

    # ──────────────────────────────────────────────────────────
    # 列表查询
    # ──────────────────────────────────────────────────────────
    def list_classes(self) -> list:
        return [
            schema_class
            for schema_class in self.schema.get("classes", [])
            if self._review_status(schema_class) != "rejected"
        ]

    def list_metrics(self) -> list:
        return self.metrics

    def list_concepts(self) -> list[dict]:
        """Return Concepts that have not been explicitly rejected."""
        return [
            concept
            for concept in self.schema.get("concepts", [])
            if self._review_status(concept) != "rejected"
        ]

    def list_dimension_groups(self) -> list[dict]:
        """Return runtime-available governed DimensionGroups."""
        return [
            group
            for group in self.schema.get("dimension_groups", [])
            if str(group.get("status") or "draft") == "approved"
        ]

    def list_relationships(self) -> list:
        return self.relationships

    def get_class_description(self, class_id: str) -> str:
        """获取 class 的中文描述"""
        for c in self.list_classes():
            if c["id"] == class_id:
                return c.get("description", c.get("name_cn", ""))
        cls = self.classes.get(class_id, {})
        return cls.get("name_cn", "")
