import unittest

from agents.ontology_chatbi.node.ontology_agent import OntologyAgent
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
        self.assertEqual(_field_map(fields, []), {"销售金额": "sales_amount"})

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
