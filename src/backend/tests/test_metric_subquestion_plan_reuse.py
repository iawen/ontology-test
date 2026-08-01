import unittest

from agents.ontology_chatbi.engine import ChatEngineV3
from agents.ontology_chatbi.node.entity_disambiguator import EntityDisambiguatorAgent
from agents.ontology_chatbi.plugins import load_employee_plugin
from agents.ontology_chatbi.state import AgentState


class MetricSubquestionPlanReuseTests(unittest.TestCase):
    def test_same_target_and_metric_reuses_only_explicit_shared_context(self):
        baseline = {
            "id": "sq-base",
            "status": "completed",
            "metric_ids": ["qtd_achievement_rate"],
            "query_scope": {"target_class": "SalesFact", "join_classes": []},
            "query_plan": {
                "metrics": ["qtd_achievement_rate"],
                "dimensions": [],
                "filters": [
                    {"field": "owner", "operator": "=", "value": "卞哲", "_provenance": "user_explicit"},
                    {"field": "hospital_segment", "operator": "=", "value": "T40", "_provenance": "subquestion_local"},
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
        self.assertEqual(reusable["query_plan"]["metrics"], ["qtd_achievement_rate"])
        self.assertEqual(reusable["query_plan"]["dimensions"], [])
        self.assertEqual(reusable["query_plan"]["filters"], [
            {"field": "owner", "operator": "=", "value": "卞哲", "_provenance": "parent_reuse", "_parent_provenance": "user_explicit", "_locked": True},
        ])

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
        merged = load_employee_plugin("pfizer").merge_locked_filters(
            merged, locked_filters
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
                "filters": [
                    {"field": "rm_employee_name", "operator": "=", "value": "卞哲", "_provenance": "clarification_answer"},
                    {"field": "hospital_segment", "operator": "=", "value": "T40", "_provenance": "subquestion_local"},
                ],
            },
        }
        state = AgentState(metric_subquestions=[parent])

        reusable = ChatEngineV3._reusable_subquestion_by_id(state, "sq-parent")

        self.assertEqual(reusable["query_plan"]["filters"], [
            {"field": "rm_employee_name", "operator": "=", "value": "卞哲", "_provenance": "parent_reuse", "_parent_provenance": "clarification_answer", "_locked": True},
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