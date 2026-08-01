import unittest

from agents.ontology_chatbi.node.clarification_requirement_builder import (
    ClarificationRequirementBuilder,
)


class _Engine:
    def __init__(self):
        self.metrics = [
            {"id": "sales", "dimension_group_ids": ["time", "area"]},
            {"id": "achievement", "dimension_group_ids": ["time"]},
        ]
        self.groups = [
            {
                "id": "time",
                "name": "时间粒度",
                "status": "approved",
                "is_required": True,
                "group_type": "time",
                "options": [
                    {"value": "month", "label": "按月", "status": "approved"},
                    {"value": "quarter", "label": "按季度", "status": "approved"},
                ],
                "field_mappings": [
                    {"option_value": "month", "class_id": "Sales", "field_name": "apmonth"},
                    {"option_value": "quarter", "class_id": "Sales", "field_name": "quarter_cd"},
                ],
            },
            {
                "id": "area",
                "name": "区域口径",
                "status": "approved",
                "is_required": True,
                "group_type": "choice",
                "options": [{"value": "hospital_province", "label": "医院所在地", "status": "approved"}],
                "field_mappings": [
                    {"option_value": "hospital_province", "class_id": "Other", "field_name": "province"},
                    {"option_value": "hospital_province", "class_id": "Sales", "field_name": "hospital_province"},
                ],
            },
        ]

    def list_metrics(self):
        return self.metrics

    def list_dimension_groups(self):
        return self.groups


class ClarificationRequirementTests(unittest.TestCase):
    def setUp(self):
        self.engine = _Engine()
        self.builder = ClarificationRequirementBuilder()
        self.unit = {
            "unit_id": "sq-1",
            "query_scope": {"target_class": "Sales", "join_classes": ["Other"]},
            "query_plan": {"metrics": ["sales", "achievement"], "filters": [], "dimensions": []},
        }

    def test_explicit_ap_slot_resolves_shared_time_requirement(self):
        requirements = self.builder.build([self.unit], "2026AP04 浙江省销量", [], self.engine)
        time_requirement = next(item for item in requirements if item["group_id"] == "time")

        self.assertEqual(time_requirement["resolution_status"], "resolved")
        self.assertEqual(time_requirement["resolved_answer"]["selection_value"], "2026AP04")
        self.assertEqual(
            {item["metric_ref"] for item in time_requirement["required_by"]},
            {"sales", "achievement"},
        )

    def test_mapping_prefers_target_scope_over_join_scope(self):
        requirements = self.builder.build([self.unit], "", [], self.engine)
        area_requirement = next(item for item in requirements if item["group_id"] == "area")
        answer = self.builder.validate_answers(
            [area_requirement],
            [{
                "requirement_id": area_requirement["requirement_id"],
                "option_value": "hospital_province",
            }],
        )
        bound = self.builder.bind_answers(self.unit, answer, [area_requirement])

        self.assertEqual(bound["query_plan"]["dimensions"], ["hospital_province"])

    def test_answers_do_not_cross_requirement_scope(self):
        requirements = self.builder.build([self.unit], "", [], self.engine)
        time_requirement = next(item for item in requirements if item["group_id"] == "time")

        accepted = self.builder.validate_answers(
            [time_requirement],
            [{"requirement_id": "not-in-checkpoint", "option_value": "month", "selection_value": "2026AP04"}],
        )

        self.assertEqual(accepted, [])

    def test_multiple_period_slots_are_a_conflict(self):
        requirements = self.builder.build(
            [self.unit],
            "2026AP03 与 2026AP04 销量对比",
            [],
            self.engine,
        )
        time_requirement = next(item for item in requirements if item["group_id"] == "time")

        self.assertEqual(time_requirement["resolution_status"], "conflict")

    def test_required_geography_keeps_text_as_candidate_evidence(self):
        requirements = self.builder.build([self.unit], "2026AP04 浙江省销量", [], self.engine)
        area_requirement = next(item for item in requirements if item["group_id"] == "area")

        self.assertEqual(area_requirement["resolution_status"], "unresolved")
        self.assertEqual(area_requirement["candidate_values"], [{
            "candidate_id": f"{area_requirement['requirement_id']}:candidate:0",
            "kind": "geography_text",
            "raw_text": "浙江省",
            "selection_value": "浙江省",
            "source": "requirement_parser",
        }])

    def test_requirement_revision_changes_with_mapping_configuration(self):
        first = self.builder.build([self.unit], "", [], self.engine)
        first_time = next(item for item in first if item["group_id"] == "time")
        self.engine.groups[0]["field_mappings"][0]["field_name"] = "accounting_period"

        second = self.builder.build([self.unit], "", [], self.engine)
        second_time = next(item for item in second if item["group_id"] == "time")

        self.assertNotEqual(first_time["schema_revision"], second_time["schema_revision"])
        self.assertNotEqual(first_time["requirement_id"], second_time["requirement_id"])

    def test_ap_period_derives_quarter_for_quarter_only_requirement(self):
        self.engine.groups[0]["field_mappings"] = [
            {"option_value": "quarter", "class_id": "Sales", "field_name": "quarter_cd"},
        ]

        requirements = self.builder.build([self.unit], "2026AP04 销量", [], self.engine)
        time_requirement = next(item for item in requirements if item["group_id"] == "time")

        self.assertEqual(time_requirement["resolution_status"], "resolved")
        self.assertEqual(time_requirement["resolved_answer"]["selection_value"], "2026Q2")
        self.assertEqual(time_requirement["resolved_answer"]["source"], "derived_from_ap")

    def test_semantic_suggestion_is_card_only_until_confirmed(self):
        requirements = self.builder.build([self.unit], "", [], self.engine)
        area_requirement = next(item for item in requirements if item["group_id"] == "area")
        area_requirement["semantic_suggestions"] = [
            {
                "requirement_id": area_requirement["requirement_id"],
                "candidate_id": "req-candidate-geography-1",
                "mapping_id": f"{area_requirement['requirement_id']}:mapping:0",
                "confidence": "high",
                "reason": "浙江省可能对应医院所在地",
            }
        ]

        card = self.builder.build_card(requirements)
        bound = self.builder.bind_answers(self.unit, [], requirements)

        question = next(item for item in card["questions"] if item["group_id"] == "area")
        self.assertEqual(question["semantic_suggestions"], area_requirement["semantic_suggestions"])
        self.assertEqual(bound["query_plan"]["filters"], [])
        self.assertEqual(bound["query_plan"]["dimensions"], [])


if __name__ == "__main__":
    unittest.main()
