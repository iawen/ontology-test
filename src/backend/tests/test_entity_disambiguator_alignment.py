import unittest

from agents.ontology_chatbi.node.entity_disambiguator import EntityDisambiguatorAgent


class FakeQueryEngine:
    values = {
        ("Sales", "负责人"): ["张三", "李四"],
        ("Sales", "客户名称"): ["上海百货有限公司", "北京商城"],
        ("Sales", "日期"): ["2026-07-24"],
    }

    def fuzzy_search_values(self, class_id, field_name, keyword, limit=30):
        return {"matched_values": self.values.get((class_id, field_name), [])}


class EntityDisambiguatorAlignmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_alignment_examples_use_entity_field_format(self):
        context = EntityDisambiguatorAgent._format_alignment_examples(
            [
                {
                    "class_id": "Sales",
                    "class_name": "销售明细",
                    "selection_fields": ["customer_name"],
                    "field_names": {"customer_name": "客户名称"},
                    "field_descriptions": {"customer_name": "客户的标准名称"},
                    "examples_by_field": {"customer_name": ["上海百货", "北京商城"]},
                }
            ]
        )

        self.assertIn("销售明细（Sales）字段列表如下：", context)
        self.assertIn("- customer_name（客户名称）：", context)
        self.assertIn("字段说明：客户的标准名称", context)
        self.assertIn("Example（3个）：上海百货、北京商城", context)

    async def test_float_example_field_is_excluded_from_alignment_context(self):
        self.assertTrue(EntityDisambiguatorAgent._is_float_example_field([1.5, 2.0]))
        self.assertFalse(EntityDisambiguatorAgent._is_float_example_field([1, 2]))
        self.assertFalse(EntityDisambiguatorAgent._is_float_example_field(["1.5", "2.0"]))

    async def test_person_requires_exact_value_without_rewrite(self):
        agent = EntityDisambiguatorAgent()
        result = await agent._align_filter_by_type(
            {"field": "人员", "operator": "=", "value": "张三"},
            "person",
            [{"class_id": "Sales", "field": "负责人"}],
            FakeQueryEngine(),
            None,
            "Sales",
            "Sales",
            ["Sales"],
            "test",
        )
        self.assertEqual(result["field"], "负责人")
        self.assertEqual(result["value"], "张三")

    async def test_person_does_not_fuzzy_rewrite_value(self):
        agent = EntityDisambiguatorAgent()
        result = await agent._align_filter_by_type(
            {"field": "人员", "operator": "=", "value": "张"},
            "person",
            [{"class_id": "Sales", "field": "负责人"}],
            FakeQueryEngine(),
            None,
            "Sales",
            "Sales",
            ["Sales"],
            "test",
        )
        self.assertEqual(result["field"], "人员")
        self.assertEqual(result["value"], "张")

    async def test_other_uses_best_fuzzy_column_and_value(self):
        agent = EntityDisambiguatorAgent()
        result = await agent._align_filter_by_type(
            {"field": "客户", "operator": "=", "value": "上海百货"},
            "other",
            [{"class_id": "Sales", "field": "客户名称"}],
            FakeQueryEngine(),
            None,
            "Sales",
            "Sales",
            ["Sales"],
            "test",
        )
        self.assertEqual(result["field"], "客户名称")
        self.assertEqual(result["value"], "上海百货有限公司")

    async def test_date_only_reformats_to_detected_database_format(self):
        agent = EntityDisambiguatorAgent()
        result = await agent._align_filter_by_type(
            {"field": "日期", "operator": "=", "value": "20260724"},
            "date",
            [{"class_id": "Sales", "field": "日期"}],
            FakeQueryEngine(),
            None,
            "Sales",
            "Sales",
            ["Sales"],
            "test",
        )
        self.assertEqual(result["field"], "日期")
        self.assertEqual(result["value"], "2026-07-24")


if __name__ == "__main__":
    unittest.main()
