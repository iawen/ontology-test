import unittest
import sqlite3
import json

from agents.ontology_chatbi.node.ontology_agent import OntologyAgent
from core.db.db_provider import (
    DbConnection,
    _migrate_metrics_target_class_to_class_id,
    _migrate_metric_definition_class_refs_to_ids,
    _migrate_metrics_to_surrogate_id,
    _remove_metrics_is_reviewed,
    _migrate_schema_classes_to_surrogate_id,
    _migrate_schema_classes_without_properties,
)
from core.ontology.schema_context import _compact_fields
from modules.schema import _field_map, _normalize_fields


class SchemaFieldKeyMigrationTests(unittest.TestCase):
    def test_new_field_shape_keeps_logical_to_physical_mapping(self):
        fields = _normalize_fields([
            {
                "name_cn": "销售金额",
                "name": "sales_amount",
                "type": "numeric",
            }
        ])

        self.assertEqual(fields, [
            {
                "name_cn": "销售金额",
                "name": "sales_amount",
                "type": "numeric",
                "description": "",
                "is_primary_key": False,
                "is_foreign_key": False,
            }
        ])
        self.assertEqual(_field_map(fields), {"销售金额": "sales_amount"})

    def test_properties_column_migration_backfills_fields_before_removal(self):
        raw_connection = sqlite3.connect(":memory:")
        raw_connection.row_factory = sqlite3.Row
        connection = DbConnection(raw_connection, "sqlite3")
        connection.executescript("""
            CREATE TABLE schema_classes (
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
            INSERT INTO schema_classes (id, scenario_id, properties)
            VALUES ('sales', 'demo', '["销售金额"]');
        """)

        _migrate_schema_classes_without_properties(connection, "sqlite3")

        fields = connection.execute(
            "SELECT fields FROM schema_classes WHERE id='sales' AND scenario_id='demo'"
        ).fetchone()["fields"]
        columns = connection.execute("PRAGMA table_info(schema_classes)").fetchall()
        self.assertEqual(
            fields,
            '[{"name_cn": "销售金额", "name": "销售金额", "type": "text", "description": "", "is_primary_key": false, "is_foreign_key": false}]',
        )
        self.assertNotIn("properties", [column["name"] for column in columns])

    def test_text_class_id_migrates_to_schema_name_with_numeric_primary_key(self):
        raw_connection = sqlite3.connect(":memory:")
        raw_connection.row_factory = sqlite3.Row
        connection = DbConnection(raw_connection, "sqlite3")
        connection.executescript("""
            CREATE TABLE schema_classes (
                id TEXT NOT NULL,
                scenario_id TEXT NOT NULL,
                name_cn TEXT DEFAULT '',
                description TEXT DEFAULT '',
                fields TEXT DEFAULT '[]',
                table_name TEXT DEFAULT '',
                primary_key TEXT DEFAULT '',
                is_reviewed INTEGER DEFAULT 0,
                review_status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id, scenario_id)
            );
            INSERT INTO schema_classes (id, scenario_id, name_cn)
            VALUES ('sales', 'demo', '销售事实表');
        """)

        _migrate_schema_classes_to_surrogate_id(connection, "sqlite3")

        row = connection.execute(
            "SELECT id, schema_name, name_cn FROM schema_classes WHERE scenario_id='demo'"
        ).fetchone()
        columns = connection.execute("PRAGMA table_info(schema_classes)").fetchall()
        self.assertIsInstance(row["id"], int)
        self.assertEqual(row["schema_name"], "sales")
        self.assertEqual(row["name_cn"], "销售事实表")
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) AS count FROM schema_classes WHERE scenario_id='demo' AND schema_name='sales'"
            ).fetchone()["count"],
            1,
        )
        self.assertIn("schema_name", [column["name"] for column in columns])

    def test_text_metric_id_migrates_to_name_with_numeric_primary_key(self):
        raw_connection = sqlite3.connect(":memory:")
        raw_connection.row_factory = sqlite3.Row
        connection = DbConnection(raw_connection, "sqlite3")
        connection.executescript("""
            CREATE TABLE metrics (
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
            INSERT INTO metrics (id, scenario_id, name, definition)
            VALUES ('total_sales', 'demo', '销售总额', '{"version": 1}');
            CREATE TABLE metric_dimension_bindings (
                metric_id TEXT NOT NULL,
                scenario_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                PRIMARY KEY (metric_id, scenario_id, group_id)
            );
            CREATE TABLE metric_concept_bindings (
                metric_id TEXT NOT NULL,
                scenario_id TEXT NOT NULL,
                concept_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'outcome',
                priority INTEGER DEFAULT 0,
                is_primary INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY (metric_id, scenario_id, concept_id)
            );
            INSERT INTO metric_dimension_bindings VALUES ('total_sales', 'demo', 'time_granularity');
            INSERT INTO metric_concept_bindings VALUES ('total_sales', 'demo', 'sales', 'outcome', 0, 1, 'pending');
        """)

        _migrate_metrics_to_surrogate_id(connection, "sqlite3")

        row = connection.execute(
            "SELECT id, name FROM metrics WHERE scenario_id='demo'"
        ).fetchone()
        columns = connection.execute("PRAGMA table_info(metrics)").fetchall()
        self.assertIsInstance(row["id"], int)
        self.assertEqual(row["name"], "销售总额")
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) AS count FROM metrics WHERE scenario_id='demo' AND name='销售总额'"
            ).fetchone()["count"],
            1,
        )
        self.assertNotIn("metric_name", [column["name"] for column in columns])
        self.assertEqual(
            connection.execute(
                "SELECT metric_id FROM metric_dimension_bindings WHERE scenario_id='demo'"
            ).fetchone()["metric_id"],
            "销售总额",
        )
        self.assertEqual(
            connection.execute(
                "SELECT metric_id FROM metric_concept_bindings WHERE scenario_id='demo'"
            ).fetchone()["metric_id"],
            "销售总额",
        )

    def test_metric_target_class_migrates_to_numeric_schema_class_id(self):
        raw_connection = sqlite3.connect(":memory:")
        raw_connection.row_factory = sqlite3.Row
        connection = DbConnection(raw_connection, "sqlite3")
        connection.executescript("""
            CREATE TABLE schema_classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schema_name TEXT NOT NULL,
                scenario_id TEXT NOT NULL,
                UNIQUE (scenario_id, schema_name)
            );
            INSERT INTO schema_classes (schema_name, scenario_id) VALUES ('SalesFact', 'demo');
            CREATE TABLE metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                UNIQUE (scenario_id, name)
            );
            INSERT INTO metrics (scenario_id, name, target_class)
            VALUES ('demo', '销售总额', 'SalesFact');
        """)

        _migrate_metrics_target_class_to_class_id(connection, "sqlite3")

        row = connection.execute(
            """SELECT metrics.target_class, schema_classes.id AS class_id
               FROM metrics JOIN schema_classes ON schema_classes.id=metrics.target_class
               WHERE metrics.scenario_id='demo'"""
        ).fetchone()
        target_column = next(
            column for column in connection.execute("PRAGMA table_info(metrics)").fetchall()
            if column["name"] == "target_class"
        )
        self.assertEqual(target_column["type"], "INTEGER")
        self.assertEqual(row["target_class"], row["class_id"])

    def test_metric_is_reviewed_migrates_to_review_status_then_is_dropped(self):
        raw_connection = sqlite3.connect(":memory:")
        raw_connection.row_factory = sqlite3.Row
        connection = DbConnection(raw_connection, "sqlite3")
        connection.executescript("""
            CREATE TABLE metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scenario_id TEXT NOT NULL,
                name TEXT NOT NULL,
                is_reviewed INTEGER DEFAULT 0,
                review_status TEXT DEFAULT 'pending'
            );
            INSERT INTO metrics (scenario_id, name, is_reviewed, review_status)
            VALUES ('demo', '通过指标', 1, 'pending'),
                   ('demo', '驳回指标', -1, 'pending');
        """)

        _remove_metrics_is_reviewed(connection, "sqlite3")

        rows = connection.execute(
            "SELECT name, review_status FROM metrics ORDER BY name"
        ).fetchall()
        columns = connection.execute("PRAGMA table_info(metrics)").fetchall()
        self.assertEqual(
            {row["name"]: row["review_status"] for row in rows},
            {"通过指标": "approved", "驳回指标": "rejected"},
        )
        self.assertNotIn("is_reviewed", [column["name"] for column in columns])

    def test_metric_definition_class_references_migrate_to_numeric_ids(self):
        raw_connection = sqlite3.connect(":memory:")
        raw_connection.row_factory = sqlite3.Row
        connection = DbConnection(raw_connection, "sqlite3")
        connection.executescript("""
            CREATE TABLE schema_classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schema_name TEXT NOT NULL,
                scenario_id TEXT NOT NULL,
                UNIQUE (scenario_id, schema_name)
            );
            INSERT INTO schema_classes (schema_name, scenario_id) VALUES ('SalesFact', 'demo');
            CREATE TABLE metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scenario_id TEXT NOT NULL,
                name TEXT NOT NULL,
                definition TEXT DEFAULT '{}'
            );
            INSERT INTO metrics (scenario_id, name, definition) VALUES (
                'demo', '销售总额',
                '{"version": 1, "anchor_class": "SalesFact", "inputs": [{"id": "input_1", "class_id": "SalesFact"}]}'
            );
        """)

        _migrate_metric_definition_class_refs_to_ids(connection)

        definition = json.loads(
            connection.execute("SELECT definition FROM metrics WHERE scenario_id='demo'").fetchone()["definition"]
        )
        class_id = connection.execute(
            "SELECT id FROM schema_classes WHERE scenario_id='demo' AND schema_name='SalesFact'"
        ).fetchone()["id"]
        self.assertEqual(definition["anchor_class"], class_id)
        self.assertEqual(definition["inputs"][0]["class_id"], class_id)

    def test_legacy_field_shape_is_normalized_to_new_shape(self):
        fields = _normalize_fields([
            {
                "name": "销售金额",
                "physical_name": "sales_amount",
                "type": "numeric",
            }
        ])

        self.assertEqual(fields[0]["name_cn"], "销售金额")
        self.assertEqual(fields[0]["name"], "sales_amount")
        self.assertNotIn("physical_name", fields[0])

    def test_schema_context_emits_new_field_shape(self):
        fields = _compact_fields(
            [{"name_cn": "销售金额", "name": "sales_amount", "type": "numeric"}],
            10,
        )

        self.assertEqual(fields, [
            {"name_cn": "销售金额", "name": "sales_amount", "type": "numeric"}
        ])

    def test_query_scope_context_uses_old_field_logical_and_physical_names(self):
        class Engine:
            def get_class_info(self, class_id):
                return {"name_cn": "主医院月度绩效", "description": ""}

            def get_field_map(self, class_id):
                return {"关键医院标签": "key_hospital_label"}

            def get_field_types(self, class_id):
                return {"key_hospital_label": "text"}

            def list_metrics(self):
                return []

        context = OntologyAgent.build_scope_context(
            {"target_class": "MainHospitalMonthlyPerformance", "join_classes": []},
            Engine(),
        )

        self.assertIn("关键医院标签(表字段=key_hospital_label; text)", context)
        self.assertNotIn("None(表字段=", context)


if __name__ == "__main__":
    unittest.main()
