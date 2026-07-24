import unittest

from agents.ontology_chatbi.node.ontology_agent import OntologyAgent
from agents.ontology_chatbi.prompt import get_query_details_planning_prompt


class FakeOntologyEngine:
    def __init__(self):
        self._metrics = [
            {
                "id": "actual_sales",
                "name": "实际销售额",
                "target_class": "SalesFact",
                "definition": {"anchor_class": "SalesFact", "inputs": []},
            },
            {
                "id": "sales_progress",
                "name": "销售达成",
                "target_class": "SalesFact",
                "definition": {
                    "version": 2,
                    "anchor_class": "SalesFact",
                    "outputs": [
                        {
                            "id": "sales_progress_rate",
                            "output_name": "销售达成率",
                            "inputs": [],
                        }
                    ],
                },
            },
        ]

    def list_metrics(self):
        return self._metrics

    @staticmethod
    def get_field_map(class_id):
        return {"月份": "month"} if class_id == "SalesFact" else {}

    @staticmethod
    def get_join_path(source_class, target_class):
        return []


class FakeQueryEngine:
    @staticmethod
    def field_available_in_class(class_id, field):
        return False


class OntologyAgentMetricIdTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeOntologyEngine()
        self.scope = {"target_class": "SalesFact", "join_classes": [], "join_paths": {}}

    def test_query_plan_keeps_metric_and_output_ids(self):
        result = OntologyAgent.validate_query_plan(
            {
                "query_mode": "aggregate",
                "metrics": ["actual_sales", "sales_progress_rate"],
                "dimensions": [],
                "filters": [],
                "having": [{"field": "actual_sales", "operator": ">", "value": 0}],
            },
            self.scope,
            [],
            self.engine,
            FakeQueryEngine(),
        )

        self.assertTrue(result["valid"])
        self.assertEqual(
            result["query_plan"]["metrics"],
            ["actual_sales", "sales_progress_rate"],
        )
        self.assertEqual(result["query_plan"]["having"][0]["field"], "actual_sales")

    def test_query_plan_rejects_metric_name(self):
        result = OntologyAgent.validate_query_plan(
            {
                "query_mode": "aggregate",
                "metrics": ["实际销售额"],
                "dimensions": [],
                "filters": [],
                "having": [],
            },
            self.scope,
            [],
            self.engine,
            FakeQueryEngine(),
        )

        self.assertFalse(result["valid"])
        self.assertIn("Metric 或并列输出 ID", result["error"])

    def test_query_detail_prompt_requires_metric_ids(self):
        prompt = get_query_details_planning_prompt("查询销售", "## Metrics\n- id=actual_sales")

        self.assertIn('"metrics":["Metric 或并列输出 ID"]', prompt)
        self.assertIn("必须填写列表中展示的 ID", prompt)
        self.assertIn("只通过该 ID 获取对应 Metric 定义", prompt)

    def test_reused_plan_keeps_parent_filters_and_metrics_when_delta_only_adds_filter(self):
        merged = OntologyAgent._merge_reusable_query_plan(
            {
                "query_mode": "aggregate",
                "metrics": ["sales_progress_rate"],
                "dimensions": [],
                "filters": [
                    {"field": "bd_employee_name", "operator": "=", "value": "卞哲"},
                    {"field": "quarter_cd", "operator": "=", "value": "2026Q1"},
                ],
                "having": [],
                "order_by": "",
            },
            {
                "query_mode": "aggregate",
                "metrics": [],
                "dimensions": [],
                "filters": [
                    {"field": "hospital_segment", "operator": "=", "value": "T40"},
                ],
                "having": [],
                "order_by": "",
            },
            reuse_metrics=True,
        )

        self.assertEqual(merged["metrics"], ["sales_progress_rate"])
        self.assertEqual(merged["filters"], [
            {"field": "bd_employee_name", "operator": "=", "value": "卞哲"},
            {"field": "quarter_cd", "operator": "=", "value": "2026Q1"},
            {"field": "hospital_segment", "operator": "=", "value": "T40"},
        ])

    def test_reused_filters_are_exempt_from_filter_validation(self):
        trusted = {"field": "legacy_owner", "operator": "=", "value": "卞哲"}
        result = OntologyAgent.validate_query_plan(
            {
                "query_mode": "aggregate",
                "metrics": ["sales_progress_rate"],
                "dimensions": [],
                "filters": [
                    trusted,
                    {"field": "hospital_segment", "operator": "=", "value": "T40"},
                ],
                "having": [],
            },
            self.scope,
            [],
            self.engine,
            FakeQueryEngine(),
            trusted_filters=[trusted],
        )

        self.assertTrue(result["valid"])


if __name__ == "__main__":
    unittest.main()
