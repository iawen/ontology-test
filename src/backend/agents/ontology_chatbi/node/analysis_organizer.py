import json
import re

from agents.ontology_chatbi.helper import get_tool_display_name, get_tool_purpose
from tools.logger import logger


class AnalysisOrganizerTool:
    """整理工具调用前的模型规划文本，生成前端分析过程事件。"""

    def __init__(self, client, model_name: str):
        self.client = client
        self.model_name = model_name

    async def organize(self, user_question: str, steps: list[dict]) -> list[dict]:
        if not steps:
            return []

        prompt = (
            "请将工具调用前大模型给出的规划文本整理为 JSON 数组，不要输出 Markdown。\n"
            "每个数组元素必须包含：tool_name、question、target_class、reasoning_process。\n"
            "target_class 必须是字符串数组；reasoning_process 必须是字符串数组。\n"
            "如果规划文本为空，请根据工具参数做简短、客观的步骤摘要。\n"
            f"用户问题：{user_question}\n"
            f"工具步骤：{json.dumps(steps, ensure_ascii=False, default=str)}"
        )
        try:
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "你是一个严谨的数据分析过程整理器，只输出可解析的 JSON 数组。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=1200,
            )
            content = response.choices[0].message.content or "[]"
            return self._parse_payload(content, user_question, steps)
        except Exception as exc:
            logger.exception("Analysis organizer failed: error=%s", str(exc))
            return self._fallback_payload(user_question, steps)

    def _parse_payload(self, content: str, user_question: str, steps: list[dict]) -> list[dict]:
        cleaned = self._strip_json_fence(content)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            return self._fallback_payload(user_question, steps)
        if not isinstance(payload, list):
            return self._fallback_payload(user_question, steps)

        normalized = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            reasoning = item.get("reasoning_process")
            target_class = item.get("target_class")
            normalized.append(
                {
                    "tool_name": str(item.get("tool_name") or ""),
                    "question": str(item.get("question") or user_question),
                    "target_class": target_class if isinstance(target_class, list) else [],
                    "reasoning_process": reasoning if isinstance(reasoning, list) else [],
                }
            )
        return normalized or self._fallback_payload(user_question, steps)

    @staticmethod
    def _strip_json_fence(content: str) -> str:
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        return content.strip()

    @staticmethod
    def _fallback_payload(user_question: str, steps: list[dict]) -> list[dict]:
        payload = []
        for step in steps:
            raw_args = step.get("arguments")
            args = raw_args if isinstance(raw_args, dict) else {}
            target_class = args.get("target_class")
            planning_text = str(step.get("planning_text") or get_tool_purpose(str(step.get("tool_name") or "")))
            payload.append(
                {
                    "tool_name": get_tool_display_name(str(step.get("tool_name") or "")),
                    "question": user_question,
                    "target_class": [target_class] if isinstance(target_class, str) and target_class else [],
                    "reasoning_process": [planning_text],
                }
            )
        return payload
