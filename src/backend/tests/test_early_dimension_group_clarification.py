import asyncio
import json
import sqlite3
import unittest
from unittest.mock import patch

from agents.ontology_chatbi.engine import ChatEngineV3
from agents.ontology_chatbi.node.clarify_agent import ClarifyAgent
from agents.ontology_chatbi.state import AgentState, State


class _OntologyEngine:
    def __init__(self, metrics, groups):
        self._metrics = metrics
        self.schema = {"dimension_groups": groups}

    def list_metrics(self):
        return self._metrics


class EarlyDimensionGroupClarificationTests(unittest.TestCase):
    def setUp(self):
        self.group = {
            "id": "time_granularity",
            "name": "统计周期",
            "status": "approved",
            "is_required": True,
            "group_type": "time",
            "options": [
                {"value": "month", "label": "按月", "status": "approved"},
                {"value": "quarter", "label": "按季", "status": "approved"},
            ],
            "field_mappings": [
                {
                    "option_value": "month",
                    "class_id": "SalesFact",
                    "field_name": "stat_period",
                },
                {
                    "option_value": "quarter",
                    "class_id": "SalesFact",
                    "field_name": "stat_period",
                },
            ],
        }
        self.metric = {
            "id": "sales_amount",
            "name": "销售额",
            "dimension_group_ids": ["time_granularity"],
        }
        self.engine = _OntologyEngine([self.metric], [self.group])

    def test_stable_candidate_with_required_time_group_requires_early_clarification(self):
        result = ClarifyAgent().precheck_metric_candidates(
            ["sales_amount"],
            {"target_class": "SalesFact", "join_classes": []},
            "查询销售额",
            self.engine,
        )

        self.assertTrue(result["eligible"])
        self.assertEqual(result["reason"], "stable_candidate_groups")
        self.assertEqual([item["id"] for item in result["unresolved_groups"]], ["time_granularity"])

    def test_explicit_ap_month_skips_early_time_clarification(self):
        result = ClarifyAgent().precheck_metric_candidates(
            ["sales_amount"],
            {"target_class": "SalesFact", "join_classes": []},
            "2026AP04 浙江省200mg 希必可销量",
            self.engine,
        )

        self.assertEqual(result["unresolved_groups"], [])
        self.assertEqual(result["resolved_selections"], [{
            "group_id": "time_granularity",
            "option_value": "month",
            "selection_value": "2026AP04",
            "source": "message_explicit_time",
        }])

    def test_multiple_explicit_periods_remain_unresolved(self):
        result = ClarifyAgent().precheck_metric_candidates(
            ["sales_amount"],
            {"target_class": "SalesFact", "join_classes": []},
            "对比 2026AP03 和 2026AP04 的销售额",
            self.engine,
        )

        self.assertEqual([item["id"] for item in result["unresolved_groups"]], ["time_granularity"])

    def test_candidate_groups_must_be_consistent_before_early_gate_blocks(self):
        second_metric = {
            "id": "sales_count",
            "name": "销售量",
            "dimension_group_ids": [],
        }
        result = ClarifyAgent().precheck_metric_candidates(
            ["sales_amount", "sales_count"],
            {"target_class": "SalesFact", "join_classes": []},
            "查询销售情况",
            _OntologyEngine([self.metric, second_metric], [self.group]),
        )

        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "candidate_groups_not_stable")

    def test_engine_keeps_stable_candidate_question_as_non_blocking_preview(self):
        chat_engine = ChatEngineV3.__new__(ChatEngineV3)
        chat_engine.clarify_agent = ClarifyAgent()
        state = AgentState(
            session_id="session",
            metric_candidates=["sales_amount"],
            query_scope={"target_class": "SalesFact", "join_classes": []},
            user_message="查询销售额",
        )

        self.assertFalse(chat_engine._prepare_early_dimension_group_clarification(state, self.engine))
        self.assertEqual(state.clarification_stage, "")
        self.assertIsNone(state.clarification)

    def test_early_checkpoint_resumes_at_query_planning(self):
        state = AgentState(
            session_id="session", query_id="query", clarification_stage="early"
        )

        next_state = ChatEngineV3.__new__(ChatEngineV3)._resume_after_clarification(state)

        self.assertEqual(next_state, State.QUERY_PLAN)
        self.assertEqual(state.clarification_stage, "")

    def test_history_excludes_persisted_messages_for_current_query(self):
        class ConnectionWithoutClose:
            def __init__(self):
                self.connection = sqlite3.connect(":memory:")
                self.connection.row_factory = sqlite3.Row
                self.connection.executescript(
                    """CREATE TABLE messages (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT,
                        role TEXT,
                        content TEXT,
                        query_id TEXT,
                        created_at TEXT
                    );
                    INSERT INTO messages VALUES ('old-user', 'session', 'user', '历史问题', 'old-query', '2026-01-01');
                    INSERT INTO messages VALUES ('current-user', 'session', 'user', '当前问题', 'current-query', '2026-01-02');"""
                )

            def execute(self, *args, **kwargs):
                return self.connection.execute(*args, **kwargs)

            def close(self):
                pass

        connection = ConnectionWithoutClose()
        try:
            with patch("agents.ontology_chatbi.engine.get_db", return_value=connection):
                history = asyncio.run(
                    ChatEngineV3.__new__(ChatEngineV3)._load_conversation_history(
                        "session", exclude_query_id="current-query"
                    )
                )
            self.assertEqual(history, [{"role": "user", "content": "历史问题"}])
        finally:
            connection.connection.close()

    def test_persisted_answers_create_immutable_audit_events(self):
        class ConnectionWithoutClose:
            def __init__(self):
                self.connection = sqlite3.connect(":memory:")
                self.connection.row_factory = sqlite3.Row
                self.connection.executescript(
                    """CREATE TABLE messages (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT,
                        role TEXT,
                        clarification TEXT,
                        created_at TEXT
                    );
                    INSERT INTO messages VALUES (
                        'card', 'session', 'assistant',
                        '{"checkpoint_id":"clarify-test"}', '2026-01-01');"""
                )

            def execute(self, *args, **kwargs):
                return self.connection.execute(*args, **kwargs)

            def commit(self):
                self.connection.commit()

            def rollback(self):
                self.connection.rollback()

            def close(self):
                pass

        connection = ConnectionWithoutClose()
        try:
            answers = [{"requirement_id": "req:v3:test", "option_value": "month", "selection_value": "2026AP04"}]
            with patch("agents.ontology_chatbi.engine.get_db", return_value=connection):
                ChatEngineV3._persist_clarification_answers("session", "clarify-test", answers)

            event = connection.connection.execute(
                "SELECT checkpoint_id, session_id, requirement_id, answer_json "
                "FROM chat_clarification_answer_events"
            ).fetchone()
            self.assertEqual(tuple(event[:3]), ("clarify-test", "session", "req:v3:test"))
            self.assertEqual(json.loads(event["answer_json"])["selection_value"], "2026AP04")
        finally:
            connection.connection.close()


if __name__ == "__main__":
    unittest.main()
