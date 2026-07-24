import hashlib
import os
import sqlite3

from configs.global_config import Cfg

IntegrityError = sqlite3.IntegrityError
OperationalError = sqlite3.OperationalError


def get_db():
    os.makedirs(os.path.dirname(Cfg.db_path), exist_ok=True)
    conn = sqlite3.connect(Cfg.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'admin',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS scenarios (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            data_dir TEXT NOT NULL,
            ontology_dir TEXT NOT NULL,
            is_active INTEGER DEFAULT 0,
            is_default INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS schema_classes (
            id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            name_cn TEXT DEFAULT '',
            description TEXT DEFAULT '',
            properties TEXT DEFAULT '[]',
            fields TEXT DEFAULT '[]',
            table_name TEXT DEFAULT '',
            primary_key TEXT DEFAULT '',
            is_reviewed INTEGER DEFAULT 0,
            review_status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id, scenario_id)
        );
        CREATE TABLE IF NOT EXISTS schema_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario_id TEXT NOT NULL,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            type TEXT DEFAULT '',
            source_key TEXT DEFAULT '',
            target_key TEXT DEFAULT '',
            join_key TEXT DEFAULT '',
            description TEXT DEFAULT '',
            is_reviewed INTEGER DEFAULT 0,
            review_status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS schema_optimization_files (
            id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            file_ext TEXT DEFAULT '',
            file_path TEXT NOT NULL,
            content_text TEXT DEFAULT '',
            content_hash TEXT DEFAULT '',
            size INTEGER DEFAULT 0,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS schema_optimization_runs (
            id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            file_ids TEXT DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'running',
            summary TEXT DEFAULT '',
            changes_json TEXT DEFAULT '{}',
            error TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT DEFAULT '',
            visualization TEXT DEFAULT '',
            answer_datasets TEXT DEFAULT '',
            steps TEXT DEFAULT '',
            action_confirm TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS chat_clarification_checkpoints (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            state_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            consumed_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS metrics (
            id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT '',
            target_class TEXT DEFAULT '',
            dimensions TEXT DEFAULT '[]',
            required_dimensions TEXT DEFAULT '[]',
            definition TEXT DEFAULT '{}',
            chart_type TEXT DEFAULT 'bar',
            sort_order INTEGER DEFAULT 0,
            is_reviewed INTEGER DEFAULT 0,
            review_status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
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
            is_reviewed INTEGER DEFAULT 0,
            review_status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id, scenario_id)
        );
        CREATE TABLE IF NOT EXISTS chart_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario_id TEXT NOT NULL,
            data_pattern TEXT NOT NULL,
            chart_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            priority INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS glossary_terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario_id TEXT NOT NULL,
            term TEXT NOT NULL,
            standard_name TEXT DEFAULT '',
            aliases TEXT DEFAULT '[]',
            description TEXT DEFAULT '',
            category TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            action_type TEXT NOT NULL DEFAULT 'notification',
            trigger_condition TEXT DEFAULT '',
            target_object TEXT DEFAULT '',
            parameters TEXT DEFAULT '{}',
            is_active INTEGER DEFAULT 1,
            requires_confirm INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
            executed_at TEXT DEFAULT CURRENT_TIMESTAMP,
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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
            context TEXT DEFAULT '{}',
            steps_json TEXT DEFAULT '[]',
            result TEXT DEFAULT '',
            triggered_by TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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
    """)

    cur = conn.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        pwd_hash = hashlib.sha256("admin123".encode()).hexdigest()
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)", ("admin", pwd_hash, "admin"))

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
        conn.executemany(
            "INSERT INTO system_settings (key, value) VALUES (?, ?)",
            default_settings,
        )

    conn.commit()
    _migrate_db(conn)
    conn.commit()
    conn.close()


def _migrate_db(conn):
    migrations = [
        ("metrics", "required_dimensions", "ALTER TABLE metrics ADD COLUMN required_dimensions TEXT DEFAULT '[]'"),
        ("metrics", "chart_type", "ALTER TABLE metrics ADD COLUMN chart_type TEXT DEFAULT 'bar'"),
        ("metrics", "definition", "ALTER TABLE metrics ADD COLUMN definition TEXT DEFAULT '{}'"),
        ("schema_relationships", "source_key", "ALTER TABLE schema_relationships ADD COLUMN source_key TEXT DEFAULT ''"),
        ("schema_relationships", "target_key", "ALTER TABLE schema_relationships ADD COLUMN target_key TEXT DEFAULT ''"),
        ("schema_relationships", "join_key", "ALTER TABLE schema_relationships ADD COLUMN join_key TEXT DEFAULT ''"),
        ("schema_relationships", "is_reviewed", "ALTER TABLE schema_relationships ADD COLUMN is_reviewed INTEGER DEFAULT 0"),
        ("schema_relationships", "review_status", "ALTER TABLE schema_relationships ADD COLUMN review_status TEXT DEFAULT 'pending'"),
        ("schema_classes", "fields", "ALTER TABLE schema_classes ADD COLUMN fields TEXT DEFAULT '[]'"),
        ("schema_classes", "table_name", "ALTER TABLE schema_classes ADD COLUMN table_name TEXT DEFAULT ''"),
        ("schema_classes", "primary_key", "ALTER TABLE schema_classes ADD COLUMN primary_key TEXT DEFAULT ''"),
        ("schema_classes", "is_reviewed", "ALTER TABLE schema_classes ADD COLUMN is_reviewed INTEGER DEFAULT 0"),
        ("schema_classes", "review_status", "ALTER TABLE schema_classes ADD COLUMN review_status TEXT DEFAULT 'pending'"),
        ("metrics", "is_reviewed", "ALTER TABLE metrics ADD COLUMN is_reviewed INTEGER DEFAULT 0"),
        ("metrics", "review_status", "ALTER TABLE metrics ADD COLUMN review_status TEXT DEFAULT 'pending'"),
        ("concepts", "is_reviewed", "ALTER TABLE concepts ADD COLUMN is_reviewed INTEGER DEFAULT 0"),
        ("concepts", "review_status", "ALTER TABLE concepts ADD COLUMN review_status TEXT DEFAULT 'pending'"),
        ("schema_optimization_runs", "started_at", "ALTER TABLE schema_optimization_runs ADD COLUMN started_at TEXT DEFAULT ''"),
    ]
    for table, column, statement in migrations:
        try:
            conn.execute(f"SELECT {column} FROM {table} LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(statement)
    for table in ("schema_classes", "schema_relationships", "metrics", "concepts"):
        conn.execute(f"UPDATE {table} SET review_status='approved' WHERE is_reviewed=1 AND (review_status IS NULL OR review_status='' OR review_status='pending')")
    conn.execute("DELETE FROM metrics WHERE definition IS NULL OR definition='' OR definition='{}'")
    for column in ("target_classes", "calculation", "formula", "filters_hint", "source_shape", "value_field", "aggregation", "metric_filters", "value_type", "display_format"):
        try:
            conn.execute(f"ALTER TABLE metrics DROP COLUMN {column}")
        except sqlite3.OperationalError:
            pass
    conn.execute("UPDATE schema_optimization_runs SET started_at=created_at WHERE started_at IS NULL OR started_at='' ")


if __name__ == "__main__":
    init_db()
