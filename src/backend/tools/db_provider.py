import hashlib
import os
import re
import sqlite3
from collections.abc import Mapping
from urllib.parse import unquote, urlparse

try:
    import psycopg2
    from psycopg2 import IntegrityError as PgIntegrityError
    from psycopg2 import OperationalError as PgOperationalError
except ImportError:
    psycopg2 = None
    PgIntegrityError = None
    PgOperationalError = None

try:
    import pymysql
    from pymysql.err import IntegrityError as MysqlIntegrityError
    from pymysql.err import OperationalError as MysqlOperationalError
except ImportError:
    pymysql = None
    MysqlIntegrityError = None
    MysqlOperationalError = None

from configs.global_config import Cfg


_DEFAULT_PG_DSN = "postgresql://postgres:postgres@localhost:5432/ontology_v2"
_DEFAULT_MYSQL_DSN = "mysql://root:root@localhost:3306/ontology_v2?charset=utf8mb4"
_INSERT_OR_REPLACE_RE = re.compile(
    r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+([a-zA-Z_][\w]*)\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_CONFLICT_COLUMNS = {
    "schema_classes": ("id", "scenario_id"),
    "metrics": ("id", "scenario_id"),
    "concepts": ("id", "scenario_id"),
    "skills": ("id", "scenario_id"),
    "system_settings": ("key",),
    "users": ("username",),
    "scenarios": ("id",),
    "actions": ("id",),
    "alert_rules": ("id",),
    "workflow_instances": ("id",),
    "workflow_step_logs": ("id",),
    "extraction_logs": ("id",),
    "audit_logs": ("id",),
    "data_connections": ("id",),
    "conversations": ("id",),
    "messages": ("id",),
}

_integrity_errors = [sqlite3.IntegrityError]
_operational_errors = [sqlite3.OperationalError]
if PgIntegrityError:
    _integrity_errors.append(PgIntegrityError)
if MysqlIntegrityError:
    _integrity_errors.append(MysqlIntegrityError)
if PgOperationalError:
    _operational_errors.append(PgOperationalError)
if MysqlOperationalError:
    _operational_errors.append(MysqlOperationalError)

IntegrityError = tuple(_integrity_errors)
OperationalError = tuple(_operational_errors)


class DbRow(Mapping):
    def __init__(self, columns, values):
        self._columns = list(columns)
        self._values = tuple(values)
        self._data = dict(zip(self._columns, self._values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def __iter__(self):
        return iter(self._columns)

    def __len__(self):
        return len(self._columns)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()


class DbCursor:
    def __init__(self, cursor, dialect):
        self._cursor = cursor
        self._dialect = dialect
        self._columns = []

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return getattr(self._cursor, "lastrowid", None)

    def execute(self, query, params=None):
        self._cursor.execute(_translate_sql(query, self._dialect), _normalize_params(params))
        self._refresh_columns()
        return self

    def executemany(self, query, params_seq):
        self._cursor.executemany(
            _translate_sql(query, self._dialect),
            [_normalize_params(params) for params in params_seq],
        )
        self._refresh_columns()
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return self._wrap_row(row) if row is not None else None

    def fetchall(self):
        return [self._wrap_row(row) for row in self._cursor.fetchall()]

    def close(self):
        self._cursor.close()

    def _refresh_columns(self):
        self._columns = [col[0] for col in self._cursor.description] if self._cursor.description else []

    def _wrap_row(self, row):
        if isinstance(row, sqlite3.Row):
            return DbRow(row.keys(), tuple(row))
        return DbRow(self._columns, row)


class DbConnection:
    def __init__(self, conn, dialect):
        self._conn = conn
        self._dialect = dialect

    def execute(self, query, params=None):
        cursor = DbCursor(self._conn.cursor(), self._dialect)
        return cursor.execute(query, params)

    def executemany(self, query, params_seq):
        cursor = DbCursor(self._conn.cursor(), self._dialect)
        return cursor.executemany(query, params_seq)

    def executescript(self, script):
        if self._dialect == "sqlite3":
            self._conn.executescript(script)
            return

        cursor = self._conn.cursor()
        try:
            for statement in _split_sql_script(script):
                cursor.execute(statement)
        finally:
            cursor.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


class DatabaseConfigError(RuntimeError):
    pass


def get_db_type():
    db_type = Cfg.db_type.lower()
    if db_type in {"sqlite", "sqlite3"}:
        return "sqlite3"
    if db_type in {"postgres", "postgresql", "pg"}:
        return "postgresql"
    if db_type in {"mysql", "mariadb"}:
        return "mysql"
    raise DatabaseConfigError(f"Unsupported DB_TYPE: {db_type}")


def get_db():
    dialect = get_db_type()
    if dialect == "sqlite3":
        conn = _connect_sqlite()
    elif dialect == "postgresql":
        conn = _connect_postgresql()
    elif dialect == "mysql":
        conn = _connect_mysql()
    else:
        raise DatabaseConfigError(f"Unsupported database dialect: {dialect}")
    return DbConnection(conn, dialect)


def init_db():
    conn = get_db()
    dialect = get_db_type()
    conn.executescript(_schema_sql(dialect))

    cur = conn.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        pwd_hash = hashlib.sha256("admin123".encode()).hexdigest()
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin", pwd_hash, "admin"),
        )

    cur = conn.execute("SELECT COUNT(*) FROM system_settings")
    if cur.fetchone()[0] == 0:
        default_settings = [
            ("llm_provider", "openai"),
            ("llm_model", "qwen-plus"),
            ("llm_api_key", ""),
            ("llm_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            ("extraction_batch_size", "5"),
            ("max_concurrent_extractions", "2"),
            ("auto_extract_on_upload", "true"),
            ("log_level", "INFO"),
        ]
        conn.executemany("INSERT INTO system_settings (key, value) VALUES (?, ?)", default_settings)

    conn.commit()
    _migrate_db(conn, dialect)
    conn.commit()
    conn.close()


def _connect_sqlite():
    db_path = getattr(Cfg, "db_path", "") or os.getenv("SQLITE_DB_PATH")
    if not db_path:
        raise DatabaseConfigError("SQLite db_path is not configured")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_postgresql():
    if psycopg2 is None:
        raise DatabaseConfigError("psycopg2-binary is required for PostgreSQL")

    return psycopg2.connect(Cfg.db_dsn)


def _connect_mysql():
    if pymysql is None:
        raise DatabaseConfigError("pymysql is required for MySQL")
    return pymysql.connect(**_parse_mysql_dsn(Cfg.db_dsn))


def _parse_mysql_dsn(dsn):
    parsed = urlparse(dsn)
    if parsed.scheme not in {"mysql", "mysql+pymysql", "mariadb"}:
        raise DatabaseConfigError("MySQL DSN must use mysql://, mysql+pymysql://, or mariadb://")
    query = dict(part.split("=", 1) for part in parsed.query.split("&") if part and "=" in part)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or "root"),
        "password": unquote(parsed.password or ""),
        "database": parsed.path.lstrip("/") or None,
        "charset": query.get("charset", "utf8mb4"),
        "autocommit": False,
    }


def _migrate_db(conn, dialect):
    if dialect == "sqlite3":
        migrations = [
            ("SELECT required_dimensions FROM metrics LIMIT 1", "ALTER TABLE metrics ADD COLUMN required_dimensions TEXT DEFAULT '[]'"),
            ("SELECT chart_type FROM metrics LIMIT 1", "ALTER TABLE metrics ADD COLUMN chart_type TEXT DEFAULT 'bar'"),
            ("SELECT source_key FROM schema_relationships LIMIT 1", "ALTER TABLE schema_relationships ADD COLUMN source_key TEXT DEFAULT ''"),
            ("SELECT target_key FROM schema_relationships LIMIT 1", "ALTER TABLE schema_relationships ADD COLUMN target_key TEXT DEFAULT ''"),
            ("SELECT join_key FROM schema_relationships LIMIT 1", "ALTER TABLE schema_relationships ADD COLUMN join_key TEXT DEFAULT ''"),
            ("SELECT fields FROM schema_classes LIMIT 1", "ALTER TABLE schema_classes ADD COLUMN fields TEXT DEFAULT '[]'"),
            ("SELECT csv_file FROM schema_classes LIMIT 1", "ALTER TABLE schema_classes ADD COLUMN csv_file TEXT DEFAULT ''"),
            ("SELECT primary_key FROM schema_classes LIMIT 1", "ALTER TABLE schema_classes ADD COLUMN primary_key TEXT DEFAULT ''"),
        ]
        for check_sql, alter_sql in migrations:
            try:
                conn.execute(check_sql)
            except sqlite3.OperationalError:
                conn.execute(alter_sql)
        return

    if dialect == "postgresql":
        migrations = [
            "ALTER TABLE metrics ADD COLUMN IF NOT EXISTS required_dimensions TEXT DEFAULT '[]'",
            "ALTER TABLE metrics ADD COLUMN IF NOT EXISTS chart_type TEXT DEFAULT 'bar'",
            "ALTER TABLE schema_relationships ADD COLUMN IF NOT EXISTS source_key TEXT DEFAULT ''",
            "ALTER TABLE schema_relationships ADD COLUMN IF NOT EXISTS target_key TEXT DEFAULT ''",
            "ALTER TABLE schema_relationships ADD COLUMN IF NOT EXISTS join_key TEXT DEFAULT ''",
            "ALTER TABLE schema_classes ADD COLUMN IF NOT EXISTS fields TEXT DEFAULT '[]'",
            "ALTER TABLE schema_classes ADD COLUMN IF NOT EXISTS csv_file TEXT DEFAULT ''",
            "ALTER TABLE schema_classes ADD COLUMN IF NOT EXISTS primary_key TEXT DEFAULT ''",
        ]
    else:
        migrations = [
            "ALTER TABLE metrics ADD COLUMN IF NOT EXISTS required_dimensions TEXT DEFAULT '[]'",
            "ALTER TABLE metrics ADD COLUMN IF NOT EXISTS chart_type TEXT DEFAULT 'bar'",
            "ALTER TABLE schema_relationships ADD COLUMN IF NOT EXISTS source_key TEXT DEFAULT ''",
            "ALTER TABLE schema_relationships ADD COLUMN IF NOT EXISTS target_key TEXT DEFAULT ''",
            "ALTER TABLE schema_relationships ADD COLUMN IF NOT EXISTS join_key TEXT DEFAULT ''",
            "ALTER TABLE schema_classes ADD COLUMN IF NOT EXISTS fields TEXT DEFAULT '[]'",
            "ALTER TABLE schema_classes ADD COLUMN IF NOT EXISTS csv_file TEXT DEFAULT ''",
            "ALTER TABLE schema_classes ADD COLUMN IF NOT EXISTS primary_key TEXT DEFAULT ''",
        ]
    for statement in migrations:
        conn.execute(statement)


def _schema_sql(dialect):
    serial_pk = {
        "sqlite3": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "postgresql": "SERIAL PRIMARY KEY",
        "mysql": "INT AUTO_INCREMENT PRIMARY KEY",
    }[dialect]
    timestamp_type = "TEXT" if dialect == "sqlite3" else "TIMESTAMP"

    return f"""
        CREATE TABLE IF NOT EXISTS users (
            id {serial_pk},
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'admin',
            created_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS scenarios (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            data_dir TEXT NOT NULL,
            ontology_dir TEXT NOT NULL,
            is_active INTEGER DEFAULT 0,
            is_default INTEGER DEFAULT 0,
            created_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS schema_classes (
            id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            name_cn TEXT DEFAULT '',
            description TEXT DEFAULT '',
            properties TEXT DEFAULT '[]',
            fields TEXT DEFAULT '[]',
            csv_file TEXT DEFAULT '',
            primary_key TEXT DEFAULT '',
            PRIMARY KEY (id, scenario_id)
        );
        CREATE TABLE IF NOT EXISTS schema_relationships (
            id {serial_pk},
            scenario_id TEXT NOT NULL,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            type TEXT DEFAULT '',
            source_key TEXT DEFAULT '',
            target_key TEXT DEFAULT '',
            join_key TEXT DEFAULT '',
            description TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            created_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP,
            updated_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT DEFAULT '',
            visualization TEXT DEFAULT '',
            steps TEXT DEFAULT '',
            action_confirm TEXT DEFAULT '',
            created_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS suggested_questions (
            id {serial_pk},
            scenario_id TEXT NOT NULL,
            question TEXT NOT NULL,
            icon TEXT DEFAULT '💬',
            sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS metrics (
            id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT '',
            target_class TEXT DEFAULT '',
            calculation TEXT DEFAULT '',
            formula TEXT DEFAULT '',
            dimensions TEXT DEFAULT '[]',
            required_dimensions TEXT DEFAULT '[]',
            filters_hint TEXT DEFAULT '',
            chart_type TEXT DEFAULT 'bar',
            sort_order INTEGER DEFAULT 0,
            PRIMARY KEY (id, scenario_id)
        );
        CREATE TABLE IF NOT EXISTS concepts (
            id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            parent_id TEXT DEFAULT '',
            level INTEGER DEFAULT 0,
            concept_type TEXT DEFAULT '',
            related_class TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            PRIMARY KEY (id, scenario_id)
        );
        CREATE TABLE IF NOT EXISTS chart_rules (
            id {serial_pk},
            scenario_id TEXT NOT NULL,
            data_pattern TEXT NOT NULL,
            chart_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            priority INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS glossary_terms (
            id {serial_pk},
            scenario_id TEXT NOT NULL,
            term TEXT NOT NULL,
            standard_name TEXT DEFAULT '',
            aliases TEXT DEFAULT '[]',
            description TEXT DEFAULT '',
            category TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            created_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS skills (
            id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            trigger_condition TEXT DEFAULT '',
            content TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            PRIMARY KEY (id, scenario_id)
        );
        CREATE TABLE IF NOT EXISTS extraction_logs (
            id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            started_at TEXT DEFAULT '',
            finished_at TEXT DEFAULT '',
            duration REAL DEFAULT 0,
            message TEXT DEFAULT '',
            trigger TEXT DEFAULT 'manual'
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
            id TEXT PRIMARY KEY,
            user_id INTEGER DEFAULT 0,
            username TEXT DEFAULT '',
            action TEXT NOT NULL,
            resource_type TEXT DEFAULT '',
            resource_id TEXT DEFAULT '',
            scenario_id TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            ip TEXT DEFAULT '',
            created_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS data_connections (
            id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            name TEXT NOT NULL,
            db_type TEXT NOT NULL DEFAULT 'postgresql',
            connection_url TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            action_type TEXT NOT NULL DEFAULT 'notification',
            trigger_condition TEXT DEFAULT '',
            target_object TEXT DEFAULT '',
            parameters TEXT DEFAULT '{{}}',
            is_active INTEGER DEFAULT 1,
            requires_confirm INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS action_logs (
            id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            action_id TEXT NOT NULL,
            action_name TEXT DEFAULT '',
            trigger_type TEXT DEFAULT 'manual',
            trigger_reason TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            result TEXT DEFAULT '',
            executed_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT DEFAULT '',
            duration REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS alert_rules (
            id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            target_class TEXT NOT NULL,
            condition_expression TEXT NOT NULL,
            action_id TEXT DEFAULT '',
            severity TEXT DEFAULT 'warning',
            is_active INTEGER DEFAULT 1,
            last_triggered_at TEXT DEFAULT '',
            trigger_count INTEGER DEFAULT 0,
            created_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS workflow_instances (
            id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            workflow_def_id TEXT DEFAULT '',
            workflow_name TEXT NOT NULL,
            action_id TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            current_step INTEGER DEFAULT 0,
            total_steps INTEGER DEFAULT 0,
            context TEXT DEFAULT '{{}}',
            steps_json TEXT DEFAULT '[]',
            result TEXT DEFAULT '',
            triggered_by TEXT DEFAULT 'manual',
            created_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP,
            updated_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS workflow_step_logs (
            id TEXT PRIMARY KEY,
            instance_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            step_name TEXT DEFAULT '',
            step_type TEXT DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'pending',
            assignee TEXT DEFAULT '',
            result TEXT DEFAULT '',
            started_at TEXT DEFAULT '',
            finished_at TEXT DEFAULT '',
            duration REAL DEFAULT 0
        );
    """


def _normalize_params(params):
    if params is None:
        return None
    if isinstance(params, tuple):
        return params
    if isinstance(params, list):
        return tuple(params)
    return params


def _translate_sql(query, dialect):
    query = _translate_insert_or_replace(query, dialect)
    if dialect in {"postgresql", "mysql"}:
        return query.replace("?", "%s")
    return query


def _translate_insert_or_replace(query, dialect):
    if dialect == "sqlite3":
        return query

    match = _INSERT_OR_REPLACE_RE.match(query)
    if not match:
        return query

    table, columns_sql, values_sql = match.groups()
    columns = [col.strip() for col in columns_sql.split(",")]
    conflict_columns = _CONFLICT_COLUMNS.get(table.lower())
    if not conflict_columns:
        raise ValueError(f"INSERT OR REPLACE is not configured for table: {table}")

    update_columns = [col for col in columns if col.strip(' \"`') not in conflict_columns]
    if dialect == "postgresql":
        if update_columns:
            assignments = ", ".join(f"{col}=EXCLUDED.{col}" for col in update_columns)
            conflict_action = f"DO UPDATE SET {assignments}"
        else:
            conflict_action = "DO NOTHING"
        return (
            f"INSERT INTO {table} ({columns_sql}) VALUES ({values_sql}) "
            f"ON CONFLICT ({', '.join(conflict_columns)}) {conflict_action}"
        )

    if update_columns:
        assignments = ", ".join(f"{col}=VALUES({col})" for col in update_columns)
        conflict_action = f"ON DUPLICATE KEY UPDATE {assignments}"
    else:
        first_column = columns[0]
        conflict_action = f"ON DUPLICATE KEY UPDATE {first_column}={first_column}"
    return f"INSERT INTO {table} ({columns_sql}) VALUES ({values_sql}) {conflict_action}"


def _split_sql_script(script):
    statements = []
    current = []
    in_single_quote = False
    for char in script:
        if char == "'":
            in_single_quote = not in_single_quote
        if char == ";" and not in_single_quote:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(char)
    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


if __name__ == "__main__":
    init_db()
