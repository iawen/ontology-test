"""
Data Query Engine v5 — Ontology To SQL 全面优化增强版
====================================================
核心优化点：
  1. 修复别名冲突：引入非重叠式全局拓扑路径别名自增机制（t0, t1, t2... 永不冲突）。
  2. 自动依赖推导：调用者无需手动传入 join_classes，引擎自动扫描维度、指标、过滤条件所需的表。
  3. 全局多表解耦：维度、指标、排序、HAVING、WHERE 完美支持多表异构字段，自动匹配对应表别名。
  4. 真实激活指标元数据：严格遵循 schema.json 的聚合方式 (SUM, AVG, COUNT 等)。
  5. 类型与注入防护增强。
"""

import csv
import json
import re
import sqlite3
import pandas as pd
from pathlib import Path
from typing import Optional, Any, Dict, List

from core.db.db_connector import create_db_engine
from core.ontology.ontology_engine import OntologyEngine

class DataQueryEngine:
    def __init__(self, engine: OntologyEngine, db_connection_url: str = ""):
        self.oe = engine
        self._conn: Optional[sqlite3.Connection] = None
        self._registered_tables: set = set()
        self.db_connection_url = db_connection_url
        self._db_engine = None

        if self.db_connection_url:
            try:
                self._db_engine = create_db_engine(self.db_connection_url)
            except ImportError:
                print("[Warning] sqlalchemy not installed, falling back to SQLite")

    def _quote_ident(self, identifier: str) -> str:
        if self._db_engine:
            return self._db_engine.dialect.identifier_preparer.quote(identifier)
        return f'"{identifier.replace(chr(34), chr(34) + chr(34))}"'

    def _quote_table(self, table_name: str) -> str:
        return ".".join(self._quote_ident(part) for part in table_name.split("."))

    def _col_ref(self, alias: str, physical_col: str) -> str:
        col = self._quote_ident(physical_col)
        return f"{alias}.{col}" if alias else col

    def _alias_ref(self, alias_name: str) -> str:
        return self._quote_ident(alias_name)

    def _get_connection(self):
        if self._db_engine:
            return self._db_engine.connect()
        if self._conn is None:
            self._conn = sqlite3.connect(":memory:")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _execute_sql(self, sql: str) -> list[dict]:
        if self._db_engine:
            from sqlalchemy import text
            with self._db_engine.connect() as conn:
                result = conn.execute(text(sql))
                columns = list(result.keys())
                return [dict(zip(columns, row)) for row in result.fetchall()]
        else:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]

    def _register_csv(self, class_id: str):
        if class_id in self._registered_tables:
            return
        info = self.oe.get_class_info(class_id)
        csv_file = info.get("csv_file", "")
        if not csv_file:
            return
        csv_path = self.oe.data_dir / csv_file
        if not csv_path.exists():
            return
        table_name = self.oe.get_table_name(class_id)
        df = pd.read_csv(str(csv_path), encoding="utf-8-sig")
        conn = self._get_connection()
        if not self._db_engine:
            df.to_sql(table_name, conn, if_exists="replace", index=False)
        self._registered_tables.add(class_id)

    def _map_field(self, class_id: str, field_name: str) -> str:
        return self.oe.map_field(class_id, field_name)

    ALLOWED_OPERATORS = {
        "=", "!=", "<>", ">", "<", ">=", "<=",
        "IN", "NOT IN", "LIKE", "NOT LIKE",
        "IS NULL", "IS NOT NULL", "BETWEEN"
    }

    def _validate_operator(self, op: str) -> str:
        op_upper = op.upper().strip()
        if op_upper not in self.ALLOWED_OPERATORS:
            raise ValueError(f"不允许的操作符: {op}")
        return op_upper

    def _escape_value(self, value: Any) -> str:
        if value is None:
            return "NULL"
        s = str(value)
        return s.replace("'", "''")

    def _build_filter_clause(self, class_id: str, f: dict, alias: str = "") -> str:
        field = f.get("field", "")
        operator = f.get("operator", "=")
        value = f.get("value")

        physical_col = self._map_field(class_id, field)
        col_expr = self._col_ref(alias, physical_col)
        op = self._validate_operator(operator)

        if op in ("IS NULL", "IS NOT NULL"):
            return f"{col_expr} {op}"

        if op == "BETWEEN":
            if isinstance(value, list) and len(value) == 2:
                low, high = value
                field_type = self.oe.get_field_type(class_id, field)
                if field_type == "numeric":
                    return f"{col_expr} BETWEEN {self._escape_value(low)} AND {self._escape_value(high)}"
                else:
                    return f"{col_expr} BETWEEN '{self._escape_value(low)}' AND '{self._escape_value(high)}'"
            raise ValueError("BETWEEN 必须搭配双元素列表")

        if op in ("IN", "NOT IN"):
            if isinstance(value, list):
                field_type = self.oe.get_field_type(class_id, field)
                if field_type == "numeric":
                    vals = ", ".join(str(self._escape_value(v)) for v in value)
                else:
                    vals = ", ".join(f"'{self._escape_value(v)}'" for v in value)
                return f"{col_expr} {op} ({vals})"
            raise ValueError(f"{op} 必须搭配列表值")

        if op in ("LIKE", "NOT LIKE"):
            return f"{col_expr} {op} '{self._escape_value(value)}'"

        field_type = self.oe.get_field_type(class_id, field)
        if field_type == "numeric":
            return f"{col_expr} {op} {self._escape_value(value)}"
        return f"{col_expr} {op} '{self._escape_value(value)}'"

    # ──────────────────────────────────────────────────────────
    # 核心优化方法：智能化执行本体论 SQL 查询
    # ──────────────────────────────────────────────────────────
    def execute_query(
        self,
        target_class: str,
        metrics: list = None,
        dimensions: list = None,
        filters: list = None,
        join_classes: list = None,
        order_by: str = "",
        limit: int = None,
        having: list = None,
    ) -> dict:
        metrics = metrics or []
        dimensions = dimensions or []
        filters = filters or []
        having = having or []
        join_classes = join_classes or []

        # ── 1. 自动化依赖推导（核心优化） ──
        # 扫描所有输入的字段，自动判定其归属的语义实体类，免除 LLM 强行指定
        discovered_classes = set(join_classes)
        
        for dim in dimensions:
            cls = self.oe.find_class_by_field(dim)
            if cls: discovered_classes.add(cls)

        for m in metrics:
            m_info = self.oe.get_metric_info(m)
            if m_info:
                discovered_classes.add(m_info.get("class_id"))
            else:
                cls = self.oe.find_class_by_field(m)
                if cls: discovered_classes.add(cls)

        for f in filters:
            cls = self.oe.find_class_by_field(f.get("field", ""))
            if cls: discovered_classes.add(cls)

        for h in having:
            cls = self.oe.find_class_by_field(h.get("field", ""))
            if cls: discovered_classes.add(cls)

        if order_by:
            for ob in order_by.split(","):
                ob_clean = ob.strip().split(" ")[0]
                cls = self.oe.find_class_by_field(ob_clean)
                if cls: discovered_classes.add(cls)

        # 移去自身
        discovered_classes.discard(target_class)

        # ── 2. 统一的数据注册与全局单态别名管理器 ──
        self._register_csv(target_class)
        alias_map = {target_class: "t0"}
        alias_counter = 1
        join_parts = []

        # 逐步扩展拓扑树，杜绝别名重叠Bug
        for jc in discovered_classes:
            self._register_csv(jc)
            path = self.oe.get_join_path(target_class, jc)
            if path:
                for rel in path:
                    s_class = rel["source"]
                    t_class = rel["target"]
                    
                    if t_class in alias_map:
                        continue  # 共享节点，跳过防止重复 JOIN
                    
                    s_alias = alias_map[s_class]
                    t_alias = f"t{alias_counter}"
                    alias_map[t_class] = t_alias
                    alias_counter += 1
                    
                    t_table = self.oe.get_table_name(t_class)
                    source_keys = [k.strip() for k in rel.get("source_key", "").split(",") if k.strip()]
                    target_keys = [k.strip() for k in rel.get("target_key", "").split(",") if k.strip()]
                    
                    if source_keys and target_keys:
                        mapped_source_keys = [self.oe.map_field(s_class, k) for k in source_keys]
                        mapped_target_keys = [self.oe.map_field(t_class, k) for k in target_keys]
                        on_parts = [f'{self._col_ref(s_alias, sk)} = {self._col_ref(t_alias, tk)}' for sk, tk in zip(mapped_source_keys, mapped_target_keys)]
                        join_sql = f'LEFT JOIN {self._quote_table(t_table)} AS {t_alias} ON {" AND ".join(on_parts)}'
                    else:
                        join_sql = f'LEFT JOIN {self._quote_table(t_table)} AS {t_alias} ON 1=1'
                    join_parts.append(join_sql)

        # ── 3. 构建多表解耦的 SELECT 与 GROUP BY ──
        select_parts = []
        group_parts = []
        
        # 维度映射 (自动匹配别名与分组)
        for dim in dimensions:
            cls = self.oe.find_class_by_field(dim) or target_class
            alias = alias_map.get(cls, "t0")
            p_col = self.oe.map_field(cls, dim)
            col_str = self._col_ref(alias, p_col)
            select_parts.append(f'{col_str} AS {self._alias_ref(dim)}')
            group_parts.append(col_str)

        # 指标映射 (严格遵循 schema.json 中定义的计算口径)
        for metric in metrics:
            m_info = self.oe.get_metric_info(metric)
            if m_info:
                cls = m_info.get("class_id", target_class)
                alias = alias_map.get(cls, "t0")
                logical_field = m_info.get("field")
                p_col = self.oe.map_field(cls, logical_field)
                agg_func = m_info.get("aggregation", "SUM").upper()
                select_parts.append(f'{agg_func}({self._col_ref(alias, p_col)}) AS {self._alias_ref(metric)}')
            else:
                # 兼容降级逻辑
                cls = self.oe.find_class_by_field(metric) or target_class
                alias = alias_map.get(cls, "t0")
                p_col = self.oe.map_field(cls, metric)
                f_type = self.oe.get_field_type(cls, metric)
                agg_func = "SUM" if f_type == "numeric" else "COUNT"
                select_parts.append(f'{agg_func}({self._col_ref(alias, p_col)}) AS {self._alias_ref(metric)}')

        # 无维度无指标时的兜底查询
        if not dimensions and not metrics:
            field_map = self.oe.get_field_map(target_class)
            for logical, physical in field_map.items():
                select_parts.append(f'{self._col_ref("t0", physical)} AS {self._alias_ref(logical)}')

        # ── 4. 构建准确的 WHERE 过滤 ──
        where_parts = []
        for f in filters:
            f_field = f.get("field", "")
            cls = self.oe.find_class_by_field(f_field) or target_class
            alias = alias_map.get(cls, "t0")
            try:
                where_parts.append(self._build_filter_clause(cls, f, alias))
            except ValueError as e:
                return {"type": "query_result", "data": [], "error": str(e), "sql": ""}

        # ── 5. 构建准确的 HAVING 聚合后过滤 ──
        having_parts = []
        for h in having:
            h_field = h.get("field", "")
            op = self._validate_operator(h.get("operator", ">"))
            val = self._escape_value(h.get("value"))
            
            m_info = self.oe.get_metric_info(h_field)
            if m_info:
                cls = m_info.get("class_id", target_class)
                alias = alias_map.get(cls, "t0")
                p_col = self.oe.map_field(cls, m_info.get("field"))
                agg = m_info.get("aggregation", "SUM").upper()
                having_parts.append(f'{agg}({self._col_ref(alias, p_col)}) {op} {val}')
            else:
                cls = self.oe.find_class_by_field(h_field) or target_class
                alias = alias_map.get(cls, "t0")
                p_col = self.oe.map_field(cls, h_field)
                f_type = self.oe.get_field_type(cls, h_field)
                agg = "SUM" if f_type == "numeric" else "COUNT"
                having_parts.append(f'{agg}({self._col_ref(alias, p_col)}) {op} {val}')

        # ── 6. 构建准确的 ORDER BY 排序 ──
        order_clause = ""
        if order_by:
            order_parts = []
            for ob in order_by.split(","):
                ob = ob.strip()
                ob_dir = "DESC" if ob.upper().endswith(" DESC") else "ASC"
                ob_clean = ob[:-5].strip() if ob_dir == "DESC" else (ob[:-4].strip() if ob.upper().endswith(" ASC") else ob)

                m_info = self.oe.get_metric_info(ob_clean)
                if m_info:
                    cls = m_info.get("class_id", target_class)
                    alias = alias_map.get(cls, "t0")
                    p_col = self.oe.map_field(cls, m_info.get("field"))
                    agg = m_info.get("aggregation", "SUM").upper()
                    order_parts.append(f'{agg}({self._col_ref(alias, p_col)}) {ob_dir}')
                else:
                    cls = self.oe.find_class_by_field(ob_clean) or target_class
                    alias = alias_map.get(cls, "t0")
                    p_col = self.oe.map_field(cls, ob_clean)
                    if ob_clean in metrics:
                        f_type = self.oe.get_field_type(cls, ob_clean)
                        agg = "SUM" if f_type == "numeric" else "COUNT"
                        order_parts.append(f'{agg}({self._col_ref(alias, p_col)}) {ob_dir}')
                    else:
                        order_parts.append(f'{self._col_ref(alias, p_col)} {ob_dir}')
            order_clause = ", ".join(order_parts)

        # ── 7. 拼装大一统 SQL ──
        sql = f"SELECT {', '.join(select_parts)}\nFROM {self._quote_table(self.oe.get_table_name(target_class))} AS t0"
        if join_parts:
            sql += "\n" + "\n".join(join_parts)
        if where_parts:
            sql += f"\nWHERE {' AND '.join(where_parts)}"
        if group_parts:
            sql += f"\nGROUP BY {', '.join(group_parts)}"
        if having_parts:
            sql += f"\nHAVING {' AND '.join(having_parts)}"
        if order_clause:
            sql += f"\nORDER BY {order_clause}"
        if limit:
            sql += f"\nLIMIT {limit}"

        print(f"Generated Optimized SQL:\n{sql}")
        
        try:
            rows = self._execute_sql(sql)
            return {
                "type": "query_result",
                "columns": list(rows[0].keys()) if rows else [],
                "rows": rows,
                "row_count": len(rows),
                "sql": sql,
                "target_class": target_class,
            }
        except Exception as e:
            return {"type": "query_result", "columns": [], "rows": [], "row_count": 0, "error": str(e), "sql": sql}

    # ──────────────────────────────────────────────────────────
    # 模糊搜索
    # ──────────────────────────────────────────────────────────
    def fuzzy_search_values(self, class_id: str, field_name: str,
                             keyword: str, limit: int = 10) -> dict:
        """模糊搜索字段值，用于实体消歧"""
        self._register_csv(class_id)

        table_name = self.oe.get_table_name(class_id)
        physical_col = self._map_field(class_id, field_name)

        # 防注入：转义 keyword
        safe_keyword = keyword.replace("'", "''")

        col = self._col_ref("", physical_col)
        sql = f"SELECT DISTINCT {col} FROM {self._quote_table(table_name)} WHERE {col} LIKE '%{safe_keyword}%' LIMIT {limit}"

        try:
            rows = self._execute_sql(sql)
            values = [row[physical_col] for row in rows]
            return {
                "type": "fuzzy_search_result",
                "class_id": class_id,
                "field_name": field_name,
                "keyword": keyword,
                "values": values,
                "count": len(values),
            }
        except Exception as e:
            return {
                "type": "fuzzy_search_result",
                "class_id": class_id,
                "field_name": field_name,
                "keyword": keyword,
                "values": [],
                "error": str(e),
            }

    # ──────────────────────────────────────────────────────────
    # 样本数据
    # ──────────────────────────────────────────────────────────
    def get_class_sample(self, class_id: str, limit: int = 5) -> dict:
        """获取 class 的样本数据"""
        self._register_csv(class_id)

        table_name = self.oe.get_table_name(class_id)
        sql = f'SELECT * FROM {self._quote_table(table_name)} LIMIT {limit}'

        try:
            rows = self._execute_sql(sql)
            return {
                "type": "sample_result",
                "class_id": class_id,
                "data": rows,
                "row_count": len(rows),
            }
        except Exception as e:
            return {
                "type": "sample_result",
                "class_id": class_id,
                "data": [],
                "error": str(e),
            }
