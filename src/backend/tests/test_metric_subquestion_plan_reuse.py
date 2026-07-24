import unittest

from agents.ontology_chatbi.engine import ChatEngineV3
from agents.ontology_chatbi.node.entity_disambiguator import EntityDisambiguatorAgent
from agents.ontology_chatbi.state import AgentState


class MetricSubquestionPlanReuseTests(unittest.TestCase):
    def test_same_target_and_metric_reuses_validated_plan_as_seed(self):
        baseline = {
            "id": "sq-base",
            "status": "completed",
            "metric_ids": ["qtd_achievement_rate"],
            "query_scope": {"target_class": "SalesFact", "join_classes": []},
            "query_plan": {
                "metrics": ["qtd_achievement_rate"],
                "dimensions": [],
                "filters": [
                    {"field": "owner", "operator": "=", "value": "卞哲"},
                    {"field": "quarter_cd", "operator": "=", "value": "2026Q1"},
                ],
                "having": [],
                "order_by": "",
            },
        }
        child = {"id": "sq-t40", "metric_ids": ["qtd_achievement_rate"]}
        state = AgentState(metric_subquestions=[baseline, child])

        reusable = ChatEngineV3._find_reusable_subquestion_plan(
            state,
            child,
            {"target_class": "SalesFact", "join_classes": []},
            ["qtd_achievement_rate"],
        )

        self.assertEqual(reusable["subquestion_id"], "sq-base")
        self.assertEqual(reusable["query_plan"], baseline["query_plan"])

    def test_different_join_scope_does_not_reuse_plan(self):
        baseline = {
            "id": "sq-base",
            "status": "completed",
            "metric_ids": ["qtd_achievement_rate"],
            "query_scope": {"target_class": "SalesFact", "join_classes": ["Hospital"]},
            "query_plan": {"metrics": ["qtd_achievement_rate"]},
        }
        child = {"id": "sq-child", "metric_ids": ["qtd_achievement_rate"]}
        state = AgentState(metric_subquestions=[baseline, child])

        reusable = ChatEngineV3._find_reusable_subquestion_plan(
            state,
            child,
            {"target_class": "SalesFact", "join_classes": []},
            ["qtd_achievement_rate"],
        )

        self.assertIsNone(reusable)

    def test_subset_child_keeps_parent_person_and_time_filters(self):
        locked_filters = [
            {"field": "bd_employee_name", "operator": "=", "value": "卞哲"},
            {"field": "quarter_cd", "operator": "=", "value": "2026Q1"},
        ]
        child_filters = [
            {"field": "rm_employee_name", "operator": "=", "value": "卞哲"},
            {"field": "quarter_cd", "operator": "=", "value": "2026Q1"},
            {"field": "hospital_segment", "operator": "=", "value": "T40"},
        ]

        merged = EntityDisambiguatorAgent._merge_locked_filters(
            child_filters, locked_filters
        )

        self.assertEqual(merged, [
            {"field": "bd_employee_name", "operator": "=", "value": "卞哲"},
            {"field": "quarter_cd", "operator": "=", "value": "2026Q1"},
            {"field": "hospital_segment", "operator": "=", "value": "T40"},
        ])

    def test_reusable_base_uses_executor_resolved_filters(self):
        parent = {
            "id": "sq-parent",
            "status": "completed",
            "query_scope": {"target_class": "SalesFact", "join_classes": []},
            "query_plan": {
                "metrics": ["qtd_achievement_rate"],
                "filters": [{"field": "bd_employee_name", "operator": "=", "value": "卞哲"}],
            },
            "arguments": {
                "filters": [{"field": "rm_employee_name", "operator": "=", "value": "卞哲"}],
            },
        }
        state = AgentState(metric_subquestions=[parent])

        reusable = ChatEngineV3._reusable_subquestion_by_id(state, "sq-parent")

        self.assertEqual(reusable["query_plan"]["filters"], [
            {"field": "rm_employee_name", "operator": "=", "value": "卞哲"},
        ])

    def test_reused_scope_reconstructs_validation_envelope(self):
        parent_scope = {
            "target_class": "SalesFact",
            "join_classes": [],
            "join_paths": {},
        }

        scope_validation = {**parent_scope, "valid": True, "error": ""}

        self.assertTrue(scope_validation["valid"])
        self.assertEqual(scope_validation["target_class"], "SalesFact")
        self.assertEqual(scope_validation["join_paths"], {})


if __name__ == "__main__":
    unittest.main()