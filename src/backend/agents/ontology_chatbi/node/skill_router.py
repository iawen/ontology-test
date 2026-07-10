import json

from tools.logger import logger
from agents.ontology_chatbi.helper import route_skills

# ============================================================
# 技能路由 Agent
# ============================================================


class SkillRouterAgent:
    """路由用户消息到相关技能包（增加鲁棒性兜底）"""

    async def route(self, scenario_id: str, user_message: str) -> list[dict]:
        try:
            res = await route_skills(scenario_id, user_message)

            # 如果返回的是字符串，尝试在 Agent 内部自解包
            if isinstance(res, str):
                try:
                    res = json.loads(res)
                except json.JSONDecodeError:
                    logger.warning("Skill route returned invalid JSON: %s", res)
                    return []

            return res if isinstance(res, list) else []
        except Exception as e:
            # 容错降级：记录日志，返回空列表，确保主流程不中断
            logger.exception("Skill route failed: scenario_id=%s error=%s", scenario_id, str(e))
            return []
