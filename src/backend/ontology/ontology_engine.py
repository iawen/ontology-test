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
        schema_path = self.ontology_dir / "schema.json"
        mapping_path = self.ontology_dir / "schema_mapping.json"
        if schema_path.exists():
            with open(schema_path, "r", encoding="utf-8") as f:
                self.schema = json.load(f)
        if mapping_path.exists():
            with open(mapping_path, "r", encoding="utf-8") as f:
                self.mapping = json.load(f)
                self.classes = self.mapping.get("classes", {})
                self.relationships = self._normalize_relationships(
                    self.mapping.get("relationships", [])
                )
        self.metrics = self.schema.get("metrics", [])

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
        """获取 class 对应的物理表名"""
        info = self.classes.get(class_id, {})
        return info.get("table_name", class_id)

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

    def get_csv_file(self, class_id: str) -> str:
        """获取 class 对应的 CSV 文件名"""
        info = self.classes.get(class_id, {})
        return info.get("csv_file", "")

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
            if m.get("id") == metric_id_or_name or m.get("name_cn") == metric_id_or_name:
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
        return self.schema.get("classes", [])

    def list_metrics(self) -> list:
        return self.metrics

    def list_relationships(self) -> list:
        return self.relationships

    def get_class_description(self, class_id: str) -> str:
        """获取 class 的中文描述"""
        for c in self.list_classes():
            if c["id"] == class_id:
                return c.get("description", c.get("name_cn", ""))
        cls = self.classes.get(class_id, {})
        return cls.get("name_cn", "")
