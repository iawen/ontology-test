"""
数据库连接器 — 统一接口支持 PostgreSQL / MySQL
================================================
基于 SQLAlchemy 实现多数据库类型的统一连接、表发现、数据读取。
设计原则：
  1. 对外暴露统一接口，调用方无需关心底层数据库类型
  2. 连接使用完毕后立即释放，不维护长连接
  3. 读取数据时限制行数，避免内存溢出
"""

import json
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import Engine


# ============================================================
# 连接管理
# ============================================================

def create_db_engine(connection_url: str) -> Engine:
    """
    根据 connection_url 创建 SQLAlchemy Engine。
    支持的 URL 格式：
      PostgreSQL: postgresql://user:pass@host:port/dbname
      MySQL:      mysql+pymysql://user:pass@host:port/dbname
    """
    # 自动补全驱动前缀
    url = connection_url.strip()
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        # psycopg2 是默认驱动，如果环境只有 psycopg2-binary 也能用
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
    elif url.startswith("mysql://"):
        url = "mysql+pymysql://" + url[len("mysql://"):]
    elif url.startswith("mysql+pymysql://"):
        pass  # 已经是完整格式
    else:
        raise ValueError(f"不支持的数据库连接格式: {url[:30]}...")

    engine = create_engine(
        url,
        pool_pre_ping=True,       # 连接前先 ping，避免断连
        pool_recycle=1800,        # 30 分钟回收连接
        connect_args={"connect_timeout": 10},
    )
    return engine


def test_connection(connection_url: str) -> dict:
    """
    测试数据库连接是否可用。
    返回 {"ok": True, "db_type": ..., "database": ...} 或 {"ok": False, "error": ...}
    """
    try:
        db_type = "postgresql" if "postgresql" in connection_url or "postgres://" in connection_url else "mysql"
        if db_type == "postgresql":
            sql = """SELECT COUNT(*) AS table_count
                FROM pg_tables 
                WHERE schemaname NOT IN ('pg_catalog', 'information_schema');"""
        else:
            sql = """SELECT COUNT(*) AS table_count
                FROM information_schema.tables
                WHERE table_schema = DATABASE();"""
        engine = create_db_engine(connection_url)

        table_count = 0
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            row = result.mappings().fetchone()
            table_count = row["table_count"]

        parsed = urlparse(connection_url)
        database = parsed.path.lstrip("/") if parsed.path else "unknown"

        engine.dispose()
        return {"ok": True, "db_type": db_type, "database": database, "table_count": table_count}
    except Exception as e:
        print(e)
        return {"ok": False, "error": str(e)}


def mask_connection_url(url: str) -> str:
    """隐藏连接 URL 中的密码，用于 API 返回"""
    try:
        parsed = urlparse(url)
        if parsed.password:
            masked = url.replace(parsed.password, "****")
            return masked
    except Exception:
        pass
    return url


def _quote_table_name(engine: Engine, table_name: str, schema: str = None) -> str:
    preparer = engine.dialect.identifier_preparer
    quoted_table = preparer.quote(table_name)
    if schema:
        return f"{preparer.quote_schema(schema)}.{quoted_table}"
    return quoted_table


# ============================================================
# 表发现
# ============================================================

def list_tables(connection_url: str, schema: str = None) -> list[dict]:
    """
    列出数据库中的所有用户表。
    返回 [{"name": "table_name", "schema": "public", "row_count": N, "columns": [...]}, ...]
    """
    engine = create_db_engine(connection_url)
    try:
        inspector = inspect(engine)
        db_type = _detect_db_type(connection_url)

        # 确定要查询的 schema
        if schema is None:
            schema = "public" if db_type == "postgresql" else None

        table_names = inspector.get_table_names(schema=schema)
        tables = []

        for tname in table_names:
            # 获取列信息
            columns_info = inspector.get_columns(tname, schema=schema)
            columns = []
            for col in columns_info:
                columns.append({
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": col.get("nullable", True),
                    "primary_key": False,  # 后面补充
                })

            # 获取主键
            pk_info = inspector.get_pk_constraint(tname, schema=schema)
            pk_columns = pk_info.get("constrained_columns", []) if pk_info else []
            for col in columns:
                if col["name"] in pk_columns:
                    col["primary_key"] = True

            # 尝试获取行数（大表可能较慢，设超时保护）
            row_count = -1
            try:
                with engine.connect() as conn:
                    full_table = _quote_table_name(engine, tname, schema)
                    result = conn.execute(text(f"SELECT COUNT(*) FROM {full_table}"))
                    row_count = result.scalar()
            except Exception:
                pass  # 获取行数失败不影响主流程

            tables.append({
                "name": tname,
                "schema": schema or "default",
                "row_count": row_count,
                "columns": columns,
            })

        return tables
    finally:
        engine.dispose()


def get_table_schema(connection_url: str, table_name: str, schema: str = None) -> dict:
    """
    获取单张表的详细结构信息。
    返回 {"name": ..., "columns": [...], "primary_key": [...], "foreign_keys": [...]}
    """
    engine = create_db_engine(connection_url)
    try:
        inspector = inspect(engine)
        db_type = _detect_db_type(connection_url)
        if schema is None:
            schema = "public" if db_type == "postgresql" else None

        columns_info = inspector.get_columns(table_name, schema=schema)
        pk_info = inspector.get_pk_constraint(table_name, schema=schema)
        fk_info = inspector.get_foreign_keys(table_name, schema=schema)

        columns = []
        for col in columns_info:
            columns.append({
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "default": str(col.get("default", "")),
                "primary_key": col["name"] in (pk_info.get("constrained_columns", []) if pk_info else []),
            })

        primary_key = pk_info.get("constrained_columns", []) if pk_info else []
        foreign_keys = []
        for fk in (fk_info or []):
            foreign_keys.append({
                "columns": fk.get("constrained_columns", []),
                "referred_table": fk.get("referred_table", ""),
                "referred_columns": fk.get("referred_columns", []),
            })

        return {
            "name": table_name,
            "schema": schema or "default",
            "columns": columns,
            "primary_key": primary_key,
            "foreign_keys": foreign_keys,
        }
    finally:
        engine.dispose()


# ============================================================
# 数据读取
# ============================================================

def read_table_sample(connection_url: str, table_name: str, sample_rows: int = 5, schema: str = None) -> dict:
    """
    读取数据库表的样本数据（类似 read_csv_summary 的输出格式）。
    返回 {
        "table_name": ...,
        "columns": [{"name": ..., "type": ...}],
        "row_count": N,
        "sample_rows": [{col: val, ...}, ...],
    }
    """
    engine = create_db_engine(connection_url)
    try:
        db_type = _detect_db_type(connection_url)
        if schema is None:
            schema = "public" if db_type == "postgresql" else None

        full_table = _quote_table_name(engine, table_name, schema)

        with engine.connect() as conn:
            # 总行数
            try:
                count_result = conn.execute(text(f"SELECT COUNT(*) FROM {full_table}"))
                row_count = count_result.scalar()
            except Exception:
                row_count = -1

            # 样本数据
            if db_type == "postgresql":
                sample_sql = f"SELECT * FROM {full_table} LIMIT :limit"
            else:
                sample_sql = f"SELECT * FROM {full_table} LIMIT :limit"

            result = conn.execute(text(sample_sql), {"limit": sample_rows})
            columns = [{"name": col, "type": ""} for col in result.keys()]
            rows = [dict(row._mapping) for row in result.fetchall()]

            # 转换不可序列化的类型
            for row in rows:
                for k, v in row.items():
                    if v is not None and not isinstance(v, (str, int, float, bool)):
                        row[k] = str(v)

        # 补充列类型信息
        try:
            col_info = get_table_schema(connection_url, table_name, schema)
            col_type_map = {c["name"]: c["type"] for c in col_info["columns"]}
            for col in columns:
                col["type"] = col_type_map.get(col["name"], "")
        except Exception:
            pass

        return {
            "table_name": table_name,
            "columns": columns,
            "row_count": row_count,
            "sample_rows": rows,
        }
    finally:
        engine.dispose()


def read_table_as_csv_summary(connection_url: str, table_name: str, schema: str = None) -> dict:
    """
    以与 read_csv_summary 兼容的格式返回数据库表信息，
    供 OntologyExtractor 直接使用。
    返回 {
        "file_name": "table_name",
        "columns": ["col1", "col2", ...],
        "column_types": {"col1": "type1", ...},
        "total_rows": N,
        "sample_rows": [{col: val, ...}, ...],
    }
    """
    sample = read_table_sample(connection_url, table_name, sample_rows=5, schema=schema)
    return {
        "file_name": table_name,
        "columns": [c["name"] for c in sample["columns"]],
        "column_types": {c["name"]: c["type"] for c in sample["columns"]},
        "total_rows": sample["row_count"],
        "sample_rows": sample["sample_rows"],
    }


def execute_query(connection_url: str, sql: str, max_rows: int = 1000) -> dict:
    """
    在外部数据库上执行 SQL 查询（只读，用于 DataQueryEngine）。
    返回 {"columns": [...], "rows": [...], "row_count": N}
    """
    engine = create_db_engine(connection_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = []
            for i, row in enumerate(result.fetchall()):
                if i >= max_rows:
                    break
                row_dict = {}
                for k, v in zip(columns, row):
                    if v is not None and not isinstance(v, (str, int, float, bool)):
                        v = str(v)
                    row_dict[k] = v
                rows.append(row_dict)

            return {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
            }
    finally:
        engine.dispose()


# ============================================================
# 内部工具
# ============================================================

def _detect_db_type(connection_url: str) -> str:
    """从连接 URL 推断数据库类型"""
    url = connection_url.lower()
    if "postgresql" in url or "postgres://" in url:
        return "postgresql"
    elif "mysql" in url:
        return "mysql"
    else:
        return "unknown"
