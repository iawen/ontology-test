"""Semantic planning support for bounded Plan-Execute orchestration."""

import json
import re
import time
from typing import cast

from openai.types.chat import ChatCompletionMessageParam

from agents.ontology_chatbi.prompt import (
    ONTOLOGY_PLANNING_SYSTEM_PROMPT,
    get_metric_evidence_judge_prompt,
    get_metric_plan_prompt,
    get_query_mode_routing_prompt,
    get_subquestion_reuse_prompt,
)
from agents.ontology_chatbi.helper import metric_plan_context_summary
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
        """Choose execution mode from the LLM's semantic reading of the full request.

        The only deterministic decision is the safety fallback for insufficient
        ontology evidence. In particular, whether related clauses are independent
        questions is a semantic decision and must not be inferred from punctuation
        or keyword matching.
        """
        has_schema_evidence = bool(schema_context.strip())
        if not has_schema_evidence or not has_metric_evidence:
            return self._with_candidate_class_ids({
                "mode": "single_query",
                "reason": "候选本体证据不足，无法安全拆分为多个受控查询。",
                "single_query_sufficient": True,
                "required_evidence": [],
                "confidence": "high",
                "decision_source": "rule",
                "matched_rule": "insufficient_ontology_evidence",
            }, user_message, schema_context)

        routing = await self._decide_query_mode_with_llm(
            user_message,
            schema_context,
            glossary_matches,
            session_id,
        )
        routing["decision_source"] = "llm"
        routing["matched_rule"] = ""
        return self._with_candidate_class_ids(routing, user_message, schema_context)

    async def _decide_query_mode_with_llm(
        self,
        user_message: str,
        schema_context: str,
        glossary_matches: list[dict],
        session_id: str,
    ) -> dict:
        payload = await self._request_json(
            "query_mode_routing",
            get_query_mode_routing_prompt(
                user_message,
                self._schema_entities_for_routing(schema_context),
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
            "candidate_class_ids": self._valid_candidate_class_ids(
                payload.get("candidate_class_ids"), schema_context
            ),
        }

    def _with_candidate_class_ids(
        self, decision: dict, user_message: str, schema_context: str
    ) -> dict:
        """Preserve LLM candidates, or provide a relaxed deterministic fallback."""
        candidates = self._valid_candidate_class_ids(
            decision.get("candidate_class_ids"), schema_context
        )
        if not candidates:
            candidates = self._heuristic_candidate_class_ids(user_message, schema_context)
        return {**decision, "candidate_class_ids": candidates}

    @staticmethod
    def _valid_candidate_class_ids(value, schema_context: str) -> list[str]:
        available = {
            entity_id
            for entity_id, _, _ in PlanExecuteAgent._schema_entity_records(schema_context)
        }
        candidates = value if isinstance(value, list) else []
        return list(
            dict.fromkeys(
                class_id
                for item in candidates
                if (class_id := str(item).strip()) in available
            )
        )[:5]

    @staticmethod
    def _heuristic_candidate_class_ids(user_message: str, schema_context: str) -> list[str]:
        question = (user_message or "").lower()
        matches = []
        for entity_id, name, description in PlanExecuteAgent._schema_entity_records(schema_context):
            entity_text = f"{entity_id} {name} {description}".lower()
            if any(token in entity_text for token in PlanExecuteAgent._question_terms(question)):
                matches.append(entity_id)
        return list(dict.fromkeys(matches))[:5]

    @staticmethod
    def _question_terms(question: str) -> list[str]:
        terms = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z_]{3,}", question)
        expanded = []
        for term in terms:
            if re.fullmatch(r"[\u4e00-\u9fff]+", term):
                expanded.extend(
                    term[start:end]
                    for start in range(len(term))
                    for end in range(start + 2, len(term) + 1)
                )
            else:
                expanded.append(term)
        return list(dict.fromkeys(expanded))

    @staticmethod
    def _schema_entities_for_routing(schema_context: str) -> str:
        """Keep the Schema portion of routing free of Metric details and physical fields."""
        entities = PlanExecuteAgent._schema_entity_records(schema_context)
        if not entities:
            return "（未检索到带描述的候选实体）"
        return "\n".join(
            f"- {entity_id}（{name or entity_id}）：{description or '无描述'}"
            for entity_id, name, description in entities
        )

    @staticmethod
    def _schema_entity_records(schema_context: str) -> list[tuple[str, str, str]]:
        entities = []
        for entity_id, name, description in re.findall(
            r"-\s+\*\*([^*]+)\*\*\(([^)]*)\):\s*([^\n]*)",
            schema_context or "",
        ):
            entity_id = entity_id.strip()
            name = name.strip()
            description = description.strip()
            if entity_id:
                entities.append((entity_id, name, description))
        return list(dict.fromkeys(entities))

    async def plan_metric_subquestions(
        self,
        user_message: str,
        glossary_matches: list[dict],
        metric_context: str,
        candidate_metrics: list[dict] | None = None,
        analysis_plan: dict | None = None,
        iteration: int = 0,
        evidence_gap: str = "",
        session_id: str = "",
    ) -> dict:
        return await self._request_json(
            "metric_plan",
            get_metric_plan_prompt(
                user_message,
                self._json_dumps(glossary_matches),
                self._metric_plan_context(candidate_metrics, metric_context),
                self._json_dumps(analysis_plan or {}),
                iteration,
                evidence_gap,
            ),
            session_id,
        )

    @staticmethod
    def _metric_plan_context(candidate_metrics: list[dict] | None, fallback: str) -> str:
        """Avoid injecting query-definition details into evidence decomposition."""
        summaries = [
            metric_plan_context_summary(metric)
            for metric in candidate_metrics or []
            if isinstance(metric, dict) and metric.get("id")
        ]
        return "\n".join(f"- {summary}" for summary in summaries) or fallback

    async def decide_subquestion_reuse(
        self,
        original_question: str,
        subquestion_intent: str,
        previous_subquestions: list[dict],
        session_id: str = "",
    ) -> dict:
        """Use semantic context to decide whether a later evidence query inherits a parent."""
        payload = await self._request_json(
            "subquestion_reuse",
            get_subquestion_reuse_prompt(
                original_question,
                subquestion_intent,
                self._json_dumps(previous_subquestions),
            ),
            session_id,
        )
        return {
            "reuse_subquestion_id": str(payload.get("reuse_subquestion_id") or "").strip(),
            "reuse_scope_and_filters": payload.get("reuse_scope_and_filters") is True,
            "reuse_metrics": payload.get("reuse_metrics") is True,
            "reason": str(payload.get("reason") or ""),
        }

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
