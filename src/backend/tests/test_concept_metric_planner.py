import unittest

from agents.ontology_chatbi.node.concept_metric_planner import ConceptMetricPlanner


class FakeOntologyEngine:
    def __init__(self):
        self._concepts = [
            {
                "id": "sales_domain",
                "name": "销售业绩",
                "description": "销售表现和趋势",
                "parent_id": "",
                "concept_type": "subject_domain",
                "related_class": "SalesFact",
                "review_status": "approved",
            },
            {
                "id": "sales_fact_group",
                "name": "销售结果",
                "description": "实际销售和目标",
                "parent_id": "sales_domain",
                "concept_type": "fact_group",
                "related_class": "SalesFact",
                "review_status": "approved",
            },
            {
                "id": "region_axis",
                "name": "区域",
                "description": "按区域分析",
                "parent_id": "sales_domain",
                "concept_type": "analysis_axis",
                "related_class": "SalesFact",
                "review_status": "approved",
            },
        ]
        self._groups = [
            {
                "id": "region",
                "name": "区域",
                "concept_id": "region_axis",
                "status": "approved",
            }
        ]
        self._metrics = [
            {
                "id": "actual_sales",
                "target_class": "SalesFact",
                "definition": {"anchor_class": "SalesFact", "inputs": []},
                "dimension_group_ids": ["region"],
                "concept_bindings": [{"concept_id": "sales_fact_group", "role": "outcome"}],
            },
            {
                "id": "sales_target",
                "target_class": "SalesFact",
                "definition": {"anchor_class": "SalesFact", "inputs": []},
                "dimension_group_ids": ["region"],
                "concept_bindings": [{"concept_id": "sales_fact_group", "role": "target"}],
            },
        ]

    def list_concepts(self):
        return self._concepts

    def list_dimension_groups(self):
        return self._groups

    def list_metrics(self):
        return self._metrics

    @staticmethod
    def get_data_source(class_id):
        return "database"


class ConceptMetricPlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = ConceptMetricPlanner()
        self.engine = FakeOntologyEngine()

    def test_builds_concept_scoped_bundle_for_trend_question(self):
        context = self.planner.build_retrieval_context("销售趋势如何", self.engine)
        plan = self.planner.build_analysis_plan(
            "销售趋势如何",
            self.engine,
            ["actual_sales", "sales_target"],
            context,
        )

        self.assertEqual(plan["analysis_type"], "trend")
        self.assertEqual(plan["domain_ids"], ["sales_domain"])
        self.assertEqual(plan["metric_bundles"][0]["metric_ids"], ["actual_sales", "sales_target"])
        self.assertEqual(plan["selected_axis_concept_ids"], ["region_axis"])

    def test_does_not_merge_metrics_with_different_fixed_filters(self):
        self.engine._metrics[1]["definition"] = {
            "anchor_class": "SalesFact",
            "inputs": [{"filters": [{"field": "plan_type", "operator": "=", "value": "annual"}]}],
        }
        context = self.planner.build_retrieval_context("销售趋势如何", self.engine)
        plan = self.planner.build_analysis_plan(
            "销售趋势如何",
            self.engine,
            ["actual_sales", "sales_target"],
            context,
        )

        self.assertEqual(len(plan["metric_bundles"]), 2)


if __name__ == "__main__":
    unittest.main()
