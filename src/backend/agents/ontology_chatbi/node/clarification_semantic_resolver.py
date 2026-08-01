"""Bounded LLM suggestions for ambiguous clarification semantics."""

from __future__ import annotations

import json

from agents.ontology_chatbi.services.prompt import (
    ONTOLOGY_PLANNING_SYSTEM_PROMPT,
    get_clarification_semantic_binding_prompt,
)
from core.llm.chat_model import get_async_client, get_model_name
from tools.logger import logger


class ClarificationSemanticResolver:
    """Return validated candidate references, never executable fields or filters."""

    async def suggest(self, requirements: list[dict], session_id: str = "") -> list[dict]:
        candidates = [item for item in requirements if item.get("resolution_status") in {"unresolved", "conflict"}]
        if not candidates:
            return []
        safe_requirements = []
        mapping_ids_by_requirement: dict[str, set[str]] = {}
        candidate_values = []
        for requirement in candidates:
            requirement_id = str(requirement["requirement_id"])
            mappings = []
            for index, mapping in enumerate(requirement.get("mappings", [])):
                mapping_id = f"{requirement_id}:mapping:{index}"
                mapping_ids_by_requirement.setdefault(requirement_id, set()).add(mapping_id)
                mappings.append({"mapping_id": mapping_id, "option_value": mapping.get("option_value")})
            for candidate in requirement.get("candidate_values", []):
                if not isinstance(candidate, dict) or not candidate.get("candidate_id"):
                    continue
                candidate_values.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "kind": candidate.get("kind") or requirement.get("group_type"),
                        "value": candidate.get("selection_value"),
                        "requirement_id": requirement_id,
                    }
                )
            safe_requirements.append(
                {
                    "requirement_id": requirement_id,
                    "group_id": requirement["group_id"],
                    "semantic_role": requirement["semantic_role"],
                    "mappings": mappings,
                }
            )
        if not candidate_values:
            return []
        try:
            response = await get_async_client().chat.completions.create(
                model=get_model_name(),
                messages=[
                    {"role": "system", "content": ONTOLOGY_PLANNING_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": get_clarification_semantic_binding_prompt(
                            json.dumps(candidate_values, ensure_ascii=False),
                            json.dumps(safe_requirements, ensure_ascii=False),
                        ),
                    },
                ],
                temperature=0,
                max_tokens=500,
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:
            logger.info("Clarification semantic suggestion unavailable: session_id=%s error=%s", session_id, str(exc))
            return []
        valid_requirement_ids = {item["requirement_id"] for item in candidates}
        valid_candidate_ids = {str(item.get("candidate_id") or "") for item in candidate_values}
        suggestions = []
        for item in payload.get("suggestions", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            requirement_id = str(item.get("requirement_id") or "")
            candidate_id = str(item.get("candidate_id") or "")
            mapping_id = str(item.get("mapping_id") or "")
            if (
                requirement_id not in valid_requirement_ids
                or candidate_id not in valid_candidate_ids
                or mapping_id not in mapping_ids_by_requirement.get(requirement_id, set())
            ):
                continue
            if str(item.get("confidence") or "").lower() not in {"high", "medium"}:
                continue
            suggestions.append(
                {
                    "requirement_id": requirement_id,
                    "candidate_id": candidate_id,
                    "mapping_id": mapping_id,
                    "confidence": str(item.get("confidence")).lower(),
                    "reason": str(item.get("reason") or ""),
                }
            )
        return suggestions
