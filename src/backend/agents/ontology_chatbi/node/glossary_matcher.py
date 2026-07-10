from agents.ontology_chatbi.helper import match_glossary_terms


# ============================================================
# 术语匹配 Agent
# ============================================================
class GlossaryMatcherAgent:
    """
    匹配用户消息中的专用术语。

    契约：
      输入: scenario_id, user_message
      输出: [{"term": str, "standard_name": str, "description": str}]
    """

    async def match(self, scenario_id: str, user_message: str) -> list[dict]:
      return match_glossary_terms(scenario_id, user_message) or []
