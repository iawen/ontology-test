"""Semantic planning support for bounded Plan-Execute orchestration."""

import json
import re
import time
from typing import cast

from openai.types.chat import ChatCompletionMessageParam

from agents.ontology_chatbi.constants import COMPARISON_QUERY_KEYWORDS
from agents.ontology_chatbi.prompt import (
    ONTOLOGY_PLANNING_SYSTEM_PROMPT,
    get_metric_evidence_judge_prompt,
    get_metric_plan_prompt,
    get_query_mode_routing_prompt,
)
from tools.logger import logger


class PlanExecuteAgent:
    """Stateless LLM helper for Plan-Execute semantic decisions and evidence planning.

    The engine owns route acceptance, execution budgets, and state transitions. This
    agent only returns constrained semantic planning payloads.
    """

    def __init__(self, client=None, model_name: str = ""):
        self.client = client
        self.model_name = model_name

    async def decide_execution_mode(
        self,
        user_message: str,
        schema_context: str,
        metric_context: str,
        glossary_matches: list[dict],
        has_metric_evidence: bool,
        session_id: str = "",
    ) -> dict:
        """Choose execution mode using deterministic multi-task rules before LLM fallback."""
        has_schema_evidence = bool(schema_context.strip())
        if not has_schema_evidence or not has_metric_evidence:
            return {
                "mode": "single_query",
                "reason": "候选本体证据不足，无法安全拆分为多个受控查询。",
                "single_query_sufficient": True,
                "required_evidence": [],
                "confidence": "high",
                "decision_source": "rule",
                "matched_rule": "insufficient_ontology_evidence",
            }

        question = user_message.lower()
        comparison_terms = [term for term in COMPARISON_QUERY_KEYWORDS if term in question]
        if comparison_terms:
            return self._rule_decision(
                "comparison_evidence",
                f"检测到比较任务关键词：{'、'.join(comparison_terms)}。",
                ["当前期间的同口径指标", "对比期间的同口径指标"],
            )

        diagnostic_terms = ("为什么", "原因", "归因", "驱动", "影响因素", "诊断", "健康度", "构成", "拆解")
        matched_diagnostic_terms = [term for term in diagnostic_terms if term in question]
        if matched_diagnostic_terms:
            return self._rule_decision(
                "diagnostic_evidence",
                f"检测到诊断/归因任务关键词：{'、'.join(matched_diagnostic_terms)}。",
                ["目标结果指标", "用于验证原因、构成或驱动的补充证据"],
            )

        routing = await self._decide_query_mode_with_llm(
            user_message,
            schema_context,
            metric_context,
            glossary_matches,
            session_id,
        )
        routing["decision_source"] = "llm"
        routing["matched_rule"] = ""
        return routing

    async def _decide_query_mode_with_llm(
        self,
        user_message: str,
        schema_context: str,
        metric_context: str,
        glossary_matches: list[dict],
        session_id: str,
    ) -> dict:
        payload = await self._request_json(
            "query_mode_routing",
            get_query_mode_routing_prompt(
                user_message,
                schema_context,
                metric_context,
                self._json_dumps(glossary_matches),
            ),
            session_id,
        )
        mode = str(payload.get("mode") or "single_query").lower()
        required_evidence = [
            str(item).strip()
            for item in payload.get("required_evidence", [])
            if isinstance(item, str) and item.strip()
        ]
        return {
            "mode": mode if mode in {"single_query", "plan_execute"} else "single_query",
            "reason": str(payload.get("reason") or ""),
            "single_query_sufficient": payload.get("single_query_sufficient") is True,
            "required_evidence": list(dict.fromkeys(required_evidence)),
            "confidence": str(payload.get("confidence") or "low").lower(),
        }

    @staticmethod
    def _rule_decision(rule: str, reason: str, required_evidence: list[str]) -> dict:
        return {
            "mode": "plan_execute",
            "reason": reason,
            "single_query_sufficient": False,
            "required_evidence": required_evidence,
            "confidence": "high",
            "decision_source": "rule",
            "matched_rule": rule,
        }

    async def plan_metric_subquestions(
        self,
        user_message: str,
        glossary_matches: list[dict],
        metric_context: str,
        iteration: int = 0,
        evidence_gap: str = "",
        session_id: str = "",
    ) -> dict:
        return await self._request_json(
            "metric_plan",
            get_metric_plan_prompt(
                user_message,
                self._json_dumps(glossary_matches),
                metric_context,
                iteration,
                evidence_gap,
            ),
            session_id,
        )

    async def judge_metric_evidence(
        self,
        user_message: str,
        metric_plan: dict,
        evidence_packet: list[dict],
        iteration: int,
        can_expand: bool,
        session_id: str = "",
    ) -> dict:
        return await self._request_json(
            "metric_evidence_judge",
            get_metric_evidence_judge_prompt(
                user_message,
                self._json_dumps(metric_plan),
                self._json_dumps(evidence_packet),
                iteration,
                can_expand,
            ),
            session_id,
        )

    async def _request_json(self, stage: str, instruction: str, session_id: str) -> dict:
        if self.client is None:
            raise RuntimeError("PlanExecuteAgent requires an LLM client for planning")
        started_at = time.time()
        response = await self.client.chat.completions.create(
            model=self.model_name,
            messages=cast(
                list[ChatCompletionMessageParam],
                [
                    {"role": "system", "content": ONTOLOGY_PLANNING_SYSTEM_PROMPT},
                    {"role": "user", "content": instruction},
                ],
            ),
            temperature=0.1,
            max_tokens=1200,
        )
        payload = self._parse_json(response.choices[0].message.content or "")
        logger.info(
            "Plan-Execute semantic stage completed: session_id=%s stage=%s duration_ms=%d valid_json=%s",
            session_id,
            stage,
            int((time.time() - started_at) * 1000),
            bool(payload),
        )
        return payload

    @staticmethod
    def _parse_json(raw: str) -> dict:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return {}
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _json_dumps(value) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)
