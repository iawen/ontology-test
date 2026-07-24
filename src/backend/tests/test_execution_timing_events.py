import unittest
from unittest.mock import patch

from agents.ontology_chatbi.engine import ChatEngineV3
from agents.ontology_chatbi.state import AgentState


class ExecutionTimingEventTests(unittest.TestCase):
    def test_routing_event_uses_measured_duration(self):
        state = AgentState(
            user_message="查看本月销售额",
            execution_mode="single_query",
            execution_mode_started_at_ms=1_000,
        )

        with patch("agents.ontology_chatbi.engine.time.time", return_value=1.275):
            ChatEngineV3._emit_execution_mode_routing_event(
                object.__new__(ChatEngineV3), state, {"decision_source": "llm"}
            )

        record = state.all_tool_results[-1]
        event = state.sse_events[-1]
        self.assertEqual(record["duration_ms"], 275)
        self.assertEqual(record["planning_duration_ms"], 275)
        self.assertEqual(event["duration"], 0.275)

    def test_metric_plan_milestone_has_no_fake_duration(self):
        state = AgentState(metric_plan={"plan_id": "plan-1"})

        ChatEngineV3._append_metric_plan_step(
            object.__new__(ChatEngineV3), state, "metric_plan", "已完成规划。", {}
        )

        record = state.all_tool_results[-1]
        self.assertNotIn("duration_ms", record)
        self.assertIsNone(state.sse_events[-1]["step"]["durationMs"])

    def test_metric_plan_milestone_includes_measured_duration(self):
        state = AgentState(metric_plan={"plan_id": "plan-1"})

        with patch("agents.ontology_chatbi.engine.time.time", return_value=1.275):
            ChatEngineV3._append_metric_plan_step(
                object.__new__(ChatEngineV3),
                state,
                "metric_plan",
                "已完成规划。",
                {},
                started_at_ms=1_000,
            )

        record = state.all_tool_results[-1]
        event = state.sse_events[-1]
        self.assertEqual(record["duration_ms"], 275)
        self.assertEqual(record["planning_duration_ms"], 275)
        self.assertEqual(event["duration"], 0.275)
        self.assertEqual(event["step"]["durationMs"], 275)


if __name__ == "__main__":
    unittest.main()