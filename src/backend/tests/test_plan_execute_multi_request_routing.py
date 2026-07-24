import unittest

from agents.ontology_chatbi.node.plan_execute_agent import PlanExecuteAgent
from agents.ontology_chatbi.prompt import get_query_mode_routing_prompt


SCHEMA_CONTEXT = "- **SalesFact**(销售事实): 销售业绩明细\n- **InventoryFact**(库存事实): 库存与可用量明细"


class RecordingPlanExecuteAgent(PlanExecuteAgent):
    def __init__(self, routing):
        super().__init__()
        self.routing = routing
        self.calls = []

    async def _decide_query_mode_with_llm(
        self, user_message, schema_context, glossary_matches, session_id
    ):
        self.calls.append((user_message, schema_context, glossary_matches, session_id))
        return self.routing


class PlanExecuteMultiRequestRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_related_independent_questions_use_plan_execute(self):
        agent = RecordingPlanExecuteAgent({
            "mode": "plan_execute",
            "reason": "销售趋势和库存充足性需要分别验证。",
            "single_query_sufficient": False,
            "required_evidence": ["本月销售趋势", "当前库存充足性"],
            "confidence": "high",
            "candidate_class_ids": ["SalesFact", "InventoryFact"],
        })

        decision = await agent.decide_execution_mode(
            "本月销售趋势如何？当前库存是否充足？",
            SCHEMA_CONTEXT,
            "metric evidence",
            [],
            True,
        )

        self.assertEqual(decision["mode"], "plan_execute")
        self.assertEqual(decision["decision_source"], "llm")
        self.assertEqual(decision["matched_rule"], "")
        self.assertEqual(len(decision["required_evidence"]), 2)
        self.assertEqual(agent.calls[0][0], "本月销售趋势如何？当前库存是否充足？")

    async def test_single_question_with_multiple_dimensions_is_also_decided_by_llm(self):
        agent = RecordingPlanExecuteAgent({
            "mode": "single_query",
            "reason": "同一销售指标按区域分组即可回答。",
            "single_query_sufficient": True,
            "required_evidence": ["本月按区域销售额"],
            "confidence": "high",
            "candidate_class_ids": ["SalesFact"],
        })

        decision = await agent.decide_execution_mode(
            "查看华东和华南本月销售额",
            SCHEMA_CONTEXT,
            "metric evidence",
            [],
            True,
        )

        self.assertEqual(decision["mode"], "single_query")
        self.assertEqual(decision["decision_source"], "llm")

    async def test_parent_and_subset_questions_use_plan_execute(self):
        agent = RecordingPlanExecuteAgent({
            "mode": "plan_execute",
            "reason": "整体达成率与 T40 子集达成率需要分别给出结论。",
            "single_query_sufficient": False,
            "required_evidence": [
                "卞哲 2026 Q1 QTD 达成率",
                "卞哲 2026 Q1 T40 QTD 达成率",
            ],
            "confidence": "high",
            "candidate_class_ids": ["SalesFact"],
        })

        decision = await agent.decide_execution_mode(
            "卞哲 2026 Q1 QTD 达成率是多少？其中 T40 的达成率是多少？",
            SCHEMA_CONTEXT,
            "metric evidence",
            [],
            True,
        )

        self.assertEqual(decision["mode"], "plan_execute")
        self.assertEqual(decision["required_evidence"], [
            "卞哲 2026 Q1 QTD 达成率",
            "卞哲 2026 Q1 T40 QTD 达成率",
        ])

    def test_routing_prompt_requires_parent_and_subset_decomposition(self):
        prompt = get_query_mode_routing_prompt("任意问题", SCHEMA_CONTEXT, "[]")

        self.assertIn("整体范围的结果", prompt)
        self.assertIn("卞哲 2026 Q1 QTD 达成率是多少？其中 T40 的达成率是多少？", prompt)
        self.assertIn("必须选择 plan_execute", prompt)

    def test_metric_plan_context_only_contains_decomposition_fields(self):
        context = PlanExecuteAgent._metric_plan_context(
            [
                {
                    "id": "qtd_achievement_rate",
                    "name": "QTD 达成率",
                    "description": "不应进入证据拆解提示词",
                    "dimensions": ["quarter_cd"],
                    "definition": {
                        "anchor_class": "SalesFact",
                        "inputs": [
                            {"output_name": "实际达成"},
                            {"output_name": "目标值"},
                        ],
                    },
                }
            ],
            "legacy verbose context",
        )

        self.assertIn("id=qtd_achievement_rate", context)
        self.assertIn("名称=QTD 达成率", context)
        self.assertIn("锚点类=SalesFact", context)
        self.assertIn("组成项名称=['实际达成', '目标值']", context)
        self.assertNotIn("不应进入证据拆解提示词", context)
        self.assertNotIn("quarter_cd", context)


if __name__ == "__main__":
    unittest.main()