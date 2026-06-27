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
import time
import uuid
import pandas as pd
from pathlib import Path
from typing import Optional, Any, Dict, List

from core.db.db_connector import create_db_engine
from core.ontology.ontology_engine import OntologyEngine
from tools.logger import logger

class DataQueryEngine:
    def __init__(self, engine: OntologyEngine, db_connection_url: str = ""):
        self.oe = engine
        self._conn: Optional[sqlite3.Connection] = None
        self._registered_tables: set = set()
        self.db_connection_url = db_connection_url
        self._db_engine = None
        self._resolved_table_names: dict[str, str] = {}

        if self.db_connection_url:
            try:
                self._db_engine = create_db_engine(self.db_connection_url)
                logger.info("DataQuery engine initialized with external db connection")
            except ImportError:
                logger.warning("sqlalchemy not installed, falling back to SQLite")

    @staticmethod
    def _log_json(value: Any, max_len: int = 2000) -> str:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
        return text if len(text) <= max_len else text[:max_len] + "...[truncated]"

    def _quote_ident(self, identifier: str) -> str:
        if identifier is None or str(identifier).strip() == "":
            raise ValueError("SQL 标识符为空：请检查指标 formula/field、字段映射或表名配置")
        identifier = str(identifier)
        if self._db_engine:
            return self._db_engine.dialect.identifier_preparer.quote(identifier)
        return f'"{identifier.replace(chr(34), chr(34) + chr(34))}"'

    def _quote_table(self, table_name: str) -> str:
        if table_name is None or str(table_name).strip() == "":
            raise ValueError("SQL 表名为空：请检查 class 的 table_name/csv_file 配置")
        table_name = str(table_name)
        return ".".join(self._quote_ident(part) for part in table_name.split("."))

    def _table_exists(self, table_name: str) -> bool:
        if not self._db_engine:
            return True
        try:
            from sqlalchemy import inspect
            inspector = inspect(self._db_engine)
            if "." in table_name:
                schema, table = table_name.rsplit(".", 1)
                return inspector.has_table(table, schema=schema)
            return inspector.has_table(table_name)
        except Exception:
            return False

    def _table_name_candidates(self, table_name: str) -> list[str]:
        base = str(table_name or "").strip()
        candidates = []
        for candidate in [base, re.sub(r"\.csv$", "", base, flags=re.IGNORECASE)]:
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        expanded = list(candidates)
        for candidate in expanded:
            normalized = re.sub(r"_\d{8,14}$", "", candidate)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    def _resolve_table_name(self, class_id: str) -> str:
        if class_id in self._resolved_table_names:
            return self._resolved_table_names[class_id]

        mapped_table = self.oe.get_table_name(class_id)
        if not self._db_engine:
            self._resolved_table_names[class_id] = mapped_table
            logger.debug(
                "DataQuery table resolved: class=%s mapped_table=%s mode=sqlite_memory",
                class_id,
                mapped_table,
            )
            return mapped_table

        candidates = self._table_name_candidates(mapped_table)
        for candidate in candidates:
            if self._table_exists(candidate):
                self._resolved_table_names[class_id] = candidate
                logger.debug(
                    "DataQuery table resolved: class=%s mapped_table=%s resolved_table=%s candidates=%s mode=external_db",
                    class_id,
                    mapped_table,
                    candidate,
                    self._log_json(candidates),
                )
                return candidate

        raise ValueError(
            f"class {class_id} 映射的物理表不存在: {mapped_table}；已尝试: {', '.join(candidates)}。"
            "请检查 schema_mapping.json 或当前场景的数据连接。"
        )

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
        started = time.time()
        if self._db_engine:
            from sqlalchemy import text
            with self._db_engine.connect() as conn:
                result = conn.execute(text(sql))
                columns = list(result.keys())
                rows = [dict(zip(columns, row)) for row in result.fetchall()]
        else:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            rows = [dict(zip(columns, row)) for row in rows]
        logger.info(
            "DataQuery SQL executed: rows=%d duration_ms=%d",
            len(rows),
            int((time.time() - started) * 1000),
        )
        return rows

    def _register_csv(self, class_id: str):
        if class_id in self._registered_tables:
            logger.debug("DataQuery CSV already registered: class=%s", class_id)
            return
        info = self.oe.get_class_info(class_id)
        csv_file = info.get("csv_file", "")
        if not csv_file:
            logger.debug("DataQuery CSV skipped: class=%s reason=no_csv_file", class_id)
            return
        csv_path = self.oe.data_dir / csv_file
        if not csv_path.exists():
            logger.warning(
                "DataQuery CSV skipped: class=%s csv_file=%s path=%s reason=file_not_found",
                class_id,
                csv_file,
                str(csv_path),
            )
            return
        table_name = self._resolve_table_name(class_id)
        df = pd.read_csv(str(csv_path), encoding="utf-8-sig")
        conn = self._get_connection()
        if not self._db_engine:
            df.to_sql(table_name, conn, if_exists="replace", index=False)
        self._registered_tables.add(class_id)
        logger.info(
            "DataQuery CSV registered: class=%s table=%s rows=%d columns=%d",
            class_id,
            table_name,
            len(df),
            len(df.columns),
        )

    def _map_field(self, class_id: str, field_name: str) -> str:
        if field_name is None or str(field_name).strip() == "":
            raise ValueError(f"字段名为空：class={class_id}")
        return self.oe.map_field(class_id, field_name)

    def _class_physical_fields(self, class_id: str) -> set[str]:
        field_map = self.oe.get_field_map(class_id)
        fields = set(field_map.values()) | set(field_map.keys())
        return {str(field) for field in fields if field}

    def _valid_mapped_keys(self, class_id: str, keys: list[str]) -> list[str]:
        physical_fields = self._class_physical_fields(class_id)
        mapped = []
        for key in keys:
            physical = self.oe.map_field(class_id, key)
            if physical in physical_fields:
                mapped.append(physical)
        return mapped

    def _build_join_condition(self, s_class: str, s_alias: str, t_class: str, t_alias: str, rel: dict) -> str:
        source_keys = [k.strip() for k in rel.get("source_key", "").split(",") if k.strip()]
        target_keys = [k.strip() for k in rel.get("target_key", "").split(",") if k.strip()]
        mapped_source_keys = self._valid_mapped_keys(s_class, source_keys)
        mapped_target_keys = self._valid_mapped_keys(t_class, target_keys)

        if not mapped_source_keys or not mapped_target_keys:
            logger.warning(
                "DataQuery join condition fallback: source=%s target=%s source_keys=%s target_keys=%s mapped_source_keys=%s mapped_target_keys=%s",
                s_class,
                t_class,
                self._log_json(source_keys),
                self._log_json(target_keys),
                self._log_json(mapped_source_keys),
                self._log_json(mapped_target_keys),
            )
            return "1=1"

        if len(mapped_source_keys) == len(mapped_target_keys):
            parts = [
                f'{self._col_ref(s_alias, sk)} = {self._col_ref(t_alias, tk)}'
                for sk, tk in zip(mapped_source_keys, mapped_target_keys)
            ]
            return " AND ".join(parts)

        if len(mapped_source_keys) == 1:
            source_col = self._col_ref(s_alias, mapped_source_keys[0])
            return " OR ".join(f'{source_col} = {self._col_ref(t_alias, tk)}' for tk in mapped_target_keys)

        if len(mapped_target_keys) == 1:
            target_col = self._col_ref(t_alias, mapped_target_keys[0])
            return " OR ".join(f'{self._col_ref(s_alias, sk)} = {target_col}' for sk in mapped_source_keys)

        parts = [
            f'{self._col_ref(s_alias, sk)} = {self._col_ref(t_alias, tk)}'
            for sk, tk in zip(mapped_source_keys, mapped_target_keys)
        ]
        return " AND ".join(parts) if parts else "1=1"

    def _metric_class(self, metric_info: dict, default_class: str) -> str:
        return metric_info.get("class_id") or metric_info.get("target_class") or default_class

    def _extract_formula_field(self, formula: str) -> Optional[str]:
        if not formula:
            return None
        match = re.search(r"\b(?:SUM|AVG|MIN|MAX|COUNT)\s*\(\s*(?:DISTINCT\s+)?([A-Za-z_][\w]*)\s*\)", formula, re.IGNORECASE)
        return match.group(1) if match else None

    def _extract_formula_aggregation(self, formula: str) -> Optional[str]:
        if not formula:
            return None
        match = re.search(r"\b(SUM|AVG|MIN|MAX|COUNT)\s*\(", formula, re.IGNORECASE)
        return match.group(1).upper() if match else None

    def _metric_expr(self, metric_info: dict, metric_name: str, default_class: str, alias_map: dict) -> str:
        cls = self._metric_class(metric_info, default_class)
        alias = alias_map.get(cls, "t0")
        formula = str(metric_info.get("formula") or "").strip()
        logical_field = metric_info.get("field") or self._extract_formula_field(formula)

        if logical_field:
            p_col = self.oe.map_field(cls, logical_field)
            agg_func = (metric_info.get("aggregation") or self._extract_formula_aggregation(formula) or "SUM").upper()
            return f'{agg_func}({self._col_ref(alias, p_col)})'

        if formula:
            return formula

        cls = self.oe.find_class_by_field(metric_name) or default_class
        alias = alias_map.get(cls, "t0")
        p_col = self.oe.map_field(cls, metric_name)
        f_type = self.oe.get_field_type(cls, metric_name)
        agg_func = "SUM" if f_type == "numeric" else "COUNT"
        return f'{agg_func}({self._col_ref(alias, p_col)})'

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
        query_id = uuid.uuid4().hex[:8]
        build_started = time.time()

        logger.info(
            "DataQuery SQL build started: query_id=%s target_class=%s metrics=%s dimensions=%s filters=%s join_classes=%s order_by=%s limit=%s having=%s mode=%s",
            query_id,
            target_class,
            self._log_json(metrics),
            self._log_json(dimensions),
            self._log_json(filters),
            self._log_json(join_classes),
            order_by,
            limit,
            self._log_json(having),
            "external_db" if self._db_engine else "sqlite_memory",
        )
        if limit is not None:
            logger.warning(
                "DataQuery LIMIT ignored: query_id=%s target_class=%s requested_limit=%s reason=query_ontology_data_should_not_truncate_results",
                query_id,
                target_class,
                limit,
            )
            limit = None

        # ── 1. 自动化依赖推导（核心优化） ──
        # 扫描所有输入的字段，自动判定其归属的语义实体类，免除 LLM 强行指定
        discovered_classes = set(join_classes)
        
        for dim in dimensions:
            cls = self.oe.find_class_by_field(dim)
            if cls:
                discovered_classes.add(cls)
                logger.debug("DataQuery dependency discovered: query_id=%s source=dimension field=%s class=%s", query_id, dim, cls)

        for m in metrics:
            m_info = self.oe.get_metric_info(m)
            if m_info:
                metric_class = self._metric_class(m_info, target_class)
                if metric_class:
                    discovered_classes.add(metric_class)
                    logger.debug("DataQuery dependency discovered: query_id=%s source=metric metric=%s class=%s", query_id, m, metric_class)
            else:
                cls = self.oe.find_class_by_field(m)
                if cls:
                    discovered_classes.add(cls)
                    logger.debug("DataQuery dependency discovered: query_id=%s source=metric_field field=%s class=%s", query_id, m, cls)

        for f in filters:
            cls = self.oe.find_class_by_field(f.get("field", ""))
            if cls:
                discovered_classes.add(cls)
                logger.debug("DataQuery dependency discovered: query_id=%s source=filter field=%s class=%s", query_id, f.get("field", ""), cls)

        for h in having:
            cls = self.oe.find_class_by_field(h.get("field", ""))
            if cls:
                discovered_classes.add(cls)
                logger.debug("DataQuery dependency discovered: query_id=%s source=having field=%s class=%s", query_id, h.get("field", ""), cls)

        if order_by:
            for ob in order_by.split(","):
                ob_clean = ob.strip().split(" ")[0]
                cls = self.oe.find_class_by_field(ob_clean)
                if cls:
                    discovered_classes.add(cls)
                    logger.debug("DataQuery dependency discovered: query_id=%s source=order_by field=%s class=%s", query_id, ob_clean, cls)

        # 移去自身
        discovered_classes.discard(target_class)
        logger.info(
            "DataQuery dependencies resolved: query_id=%s discovered_classes=%s",
            query_id,
            self._log_json(sorted(discovered_classes)),
        )

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
                logger.info(
                    "DataQuery join path found: query_id=%s target_class=%s join_class=%s path=%s",
                    query_id,
                    target_class,
                    jc,
                    self._log_json([
                        {
                            "source": rel.get("source"),
                            "target": rel.get("target"),
                            "type": rel.get("type"),
                            "source_key": rel.get("source_key"),
                            "target_key": rel.get("target_key"),
                        }
                        for rel in path
                    ]),
                )
                for rel in path:
                    s_class = rel["source"]
                    t_class = rel["target"]
                    self._register_csv(s_class)
                    self._register_csv(t_class)
                    
                    if t_class in alias_map:
                        continue  # 共享节点，跳过防止重复 JOIN
                    
                    s_alias = alias_map[s_class]
                    t_alias = f"t{alias_counter}"
                    alias_map[t_class] = t_alias
                    alias_counter += 1
                    
                    t_table = self._resolve_table_name(t_class)
                    on_clause = self._build_join_condition(s_class, s_alias, t_class, t_alias, rel)
                    join_sql = f'LEFT JOIN {self._quote_table(t_table)} AS {t_alias} ON {on_clause}'
                    join_parts.append(join_sql)
                    logger.debug(
                        "DataQuery join added: query_id=%s source=%s source_alias=%s target=%s target_alias=%s table=%s on=%s",
                        query_id,
                        s_class,
                        s_alias,
                        t_class,
                        t_alias,
                        t_table,
                        on_clause,
                    )
            elif jc != target_class:
                logger.warning(
                    "DataQuery join path missing: query_id=%s target_class=%s join_class=%s",
                    query_id,
                    target_class,
                    jc,
                )

        logger.info(
            "DataQuery alias map built: query_id=%s alias_map=%s join_count=%d",
            query_id,
            self._log_json(alias_map),
            len(join_parts),
        )

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
            logger.debug(
                "DataQuery dimension mapped: query_id=%s dimension=%s class=%s alias=%s physical_col=%s",
                query_id,
                dim,
                cls,
                alias,
                p_col,
            )

        # 指标映射 (严格遵循 schema.json 中定义的计算口径)
        for metric in metrics:
            m_info = self.oe.get_metric_info(metric)
            if m_info:
                metric_expr = self._metric_expr(m_info, metric, target_class, alias_map)
                select_parts.append(f'{metric_expr} AS {self._alias_ref(metric)}')
                logger.debug(
                    "DataQuery metric mapped: query_id=%s metric=%s class=%s expression=%s",
                    query_id,
                    metric,
                    self._metric_class(m_info, target_class),
                    metric_expr,
                )
            else:
                # 兼容降级逻辑
                cls = self.oe.find_class_by_field(metric) or target_class
                alias = alias_map.get(cls, "t0")
                p_col = self.oe.map_field(cls, metric)
                f_type = self.oe.get_field_type(cls, metric)
                agg_func = "SUM" if f_type == "numeric" else "COUNT"
                select_parts.append(f'{agg_func}({self._col_ref(alias, p_col)}) AS {self._alias_ref(metric)}')
                logger.debug(
                    "DataQuery metric fallback mapped: query_id=%s metric=%s class=%s alias=%s physical_col=%s aggregation=%s",
                    query_id,
                    metric,
                    cls,
                    alias,
                    p_col,
                    agg_func,
                )

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
                filter_clause = self._build_filter_clause(cls, f, alias)
                where_parts.append(filter_clause)
                logger.debug(
                    "DataQuery filter mapped: query_id=%s filter=%s class=%s alias=%s clause=%s",
                    query_id,
                    self._log_json(f),
                    cls,
                    alias,
                    filter_clause,
                )
            except ValueError as e:
                logger.warning(
                    "DataQuery filter build failed: query_id=%s filter=%s error=%s",
                    query_id,
                    self._log_json(f),
                    str(e),
                )
                return {"type": "query_result", "data": [], "error": str(e), "sql": ""}

        # ── 5. 构建准确的 HAVING 聚合后过滤 ──
        having_parts = []
        for h in having:
            h_field = h.get("field", "")
            op = self._validate_operator(h.get("operator", ">"))
            val = self._escape_value(h.get("value"))
            
            m_info = self.oe.get_metric_info(h_field)
            if m_info:
                having_clause = f'{self._metric_expr(m_info, h_field, target_class, alias_map)} {op} {val}'
                having_parts.append(having_clause)
            else:
                cls = self.oe.find_class_by_field(h_field) or target_class
                alias = alias_map.get(cls, "t0")
                p_col = self.oe.map_field(cls, h_field)
                f_type = self.oe.get_field_type(cls, h_field)
                agg = "SUM" if f_type == "numeric" else "COUNT"
                having_clause = f'{agg}({self._col_ref(alias, p_col)}) {op} {val}'
                having_parts.append(having_clause)
            logger.debug(
                "DataQuery having mapped: query_id=%s having=%s clause=%s",
                query_id,
                self._log_json(h),
                having_clause,
            )

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
                    order_part = f'{self._metric_expr(m_info, ob_clean, target_class, alias_map)} {ob_dir}'
                    order_parts.append(order_part)
                else:
                    cls = self.oe.find_class_by_field(ob_clean) or target_class
                    alias = alias_map.get(cls, "t0")
                    p_col = self.oe.map_field(cls, ob_clean)
                    if ob_clean in metrics:
                        f_type = self.oe.get_field_type(cls, ob_clean)
                        agg = "SUM" if f_type == "numeric" else "COUNT"
                        order_part = f'{agg}({self._col_ref(alias, p_col)}) {ob_dir}'
                        order_parts.append(order_part)
                    else:
                        order_part = f'{self._col_ref(alias, p_col)} {ob_dir}'
                        order_parts.append(order_part)
                logger.debug(
                    "DataQuery order mapped: query_id=%s order_by_item=%s clause=%s",
                    query_id,
                    ob,
                    order_part,
                )
            order_clause = ", ".join(order_parts)

        # ── 7. 拼装大一统 SQL ──
        sql = f"SELECT {', '.join(select_parts)}\nFROM {self._quote_table(self._resolve_table_name(target_class))} AS t0"
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
        logger.info(
            "DataQuery SQL built: query_id=%s build_duration_ms=%d select_count=%d join_count=%d where_count=%d group_count=%d having_count=%d order_by=%s limit=%s sql=\n%s",
            query_id,
            int((time.time() - build_started) * 1000),
            len(select_parts),
            len(join_parts),
            len(where_parts),
            len(group_parts),
            len(having_parts),
            order_clause,
            limit,
            sql,
        )
        
        try:
            rows = self._execute_sql(sql)
            logger.info(
                "DataQuery query completed: query_id=%s row_count=%d",
                query_id,
                len(rows),
            )
            return {
                "type": "query_result",
                "columns": list(rows[0].keys()) if rows else [],
                "rows": rows,
                "row_count": len(rows),
                "sql": sql,
                "target_class": target_class,
            }
        except Exception as e:
            logger.exception(
                "DataQuery query failed: query_id=%s target_class=%s error=%s sql=\n%s",
                query_id,
                target_class,
                str(e),
                sql,
            )
            return {"type": "query_result", "columns": [], "rows": [], "row_count": 0, "error": str(e), "sql": sql}

    # ──────────────────────────────────────────────────────────
    # 模糊搜索
    # ──────────────────────────────────────────────────────────
    def fuzzy_search_values(self, class_id: str, field_name: str,
                             keyword: str, limit: int = 10) -> dict:
        """模糊搜索字段值，用于实体消歧"""
        self._register_csv(class_id)

        table_name = self._resolve_table_name(class_id)
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

        table_name = self._resolve_table_name(class_id)
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
