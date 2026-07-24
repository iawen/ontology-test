"""
Chat v3 - 无状态子智能体
========================
设计原则：
  1. 严格无状态：子智能体不持有任何全局状态，只做计算
  2. 输入输出契约：明确输入参数和返回类型
  3. 可独立测试：每个子智能体可单独单元测试

子智能体清单：
  - SchemaRetrieverAgent: 动态检索相关 Schema
  - GlossaryMatcherAgent: 术语匹配
  - SkillRouterAgent: 技能路由
  - ContextCompressorAgent: 上下文压缩
  - EntityDisambiguatorAgent: 实体消歧（4级匹配）
  - ToolExecutor: 工具执行器（含后置自动校正）
"""

import json

from tools.logger import logger
from agents.tools.python_analyize import python_analyze

from .entity_disambiguator import EntityDisambiguatorAgent

# ============================================================
# 工具执行器（含后置自动校正）
# ============================================================


class ToolExecutor:
    """
    工具执行器：执行工具调用，含后置自动校正。

    死循环防线：
      - 每次工具调用打上 retry_count 标记
      - 严格限制最大重试次数为 1 次
      - 再次失败走向 CLARIFY 或抛出异常
    """

    MAX_RETRY = 1  # 死循环防线

    def __init__(
        self,
        scenario_id: str,
        entity_agent: EntityDisambiguatorAgent,
    ):
        self.scenario_id = scenario_id
        self.entity_agent = entity_agent

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        query_engine,
        engine,  # OntologyEngine
        retry_count: int = 0,
    ) -> dict:
        """
        执行工具调用。

        Args:
            retry_count: 当前重试次数（死循环防线）
        """
        try:
            if tool_name == "query_ontology_data":
                prepared_arguments = await self.entity_agent.prepare_query_ontology_data_args(
                    arguments,
                    query_engine,
                    engine,
                    scenario_id=self.scenario_id,
                )
                # The caller's subquestion ledger must retain the actual resolved
                # filter fields used for SQL, not the pre-alignment LLM proposal.
                arguments.clear()
                arguments.update(prepared_arguments)
                if arguments.get("error"):
                    return arguments

            result = self._dispatch_tool(tool_name, arguments, query_engine, engine)

            # 后置自动校正：query_ontology_data 失败时尝试修正参数
            if tool_name == "query_ontology_data" and result.get("error") and retry_count < self.MAX_RETRY:
                corrected_args = await self.entity_agent.auto_correct_query_ontology_data_args(
                    arguments,
                    query_engine,
                    engine,
                    scenario_id=self.scenario_id,
                )
                if corrected_args.get("error"):
                    return corrected_args
                if corrected_args != arguments:
                    logger.info(
                        "Tool args auto-corrected: scenario_id=%s tool=%s original=%s corrected=%s",
                        self.scenario_id,
                        tool_name,
                        json.dumps(arguments, ensure_ascii=False, default=str)[:1000],
                        json.dumps(corrected_args, ensure_ascii=False, default=str)[:1000],
                    )
                    return await self.execute(tool_name, corrected_args, query_engine, engine, retry_count + 1)

            return result

        except Exception as e:
            if retry_count < self.MAX_RETRY:
                logger.warning(
                    "Tool execution failed, retrying: scenario_id=%s tool=%s retry=%d error=%s",
                    self.scenario_id,
                    tool_name,
                    retry_count + 1,
                    str(e),
                )
                return await self.execute(tool_name, arguments, query_engine, engine, retry_count + 1)
            logger.exception(
                "Tool execution failed after retries: scenario_id=%s tool=%s error=%s",
                self.scenario_id,
                tool_name,
                str(e),
            )
            return {"error": f"工具执行失败（已重试{retry_count}次）: {str(e)}"}

    def _dispatch_tool(self, name: str, args: dict, query_engine, engine) -> dict:
        """工具分发执行"""
        if name == "query_ontology_data":
            if args.get("limit") is not None:
                logger.warning(
                    "Ignoring limit for query_ontology_data to avoid incomplete analysis: scenario_id=%s limit=%s",
                    self.scenario_id,
                    args.get("limit"),
                )
            return query_engine.execute_query(
                target_class=args.get("target_class", ""),
                metrics=args.get("metrics", []),
                dimensions=args.get("dimensions", []),
                filters=args.get("filters", []),
                join_classes=args.get("join_classes", []),
                order_by=args.get("order_by", ""),
                limit=None,
                having=args.get("having", []),
                user_question=str(args.get("user_question") or ""),
            )

        elif name == "python_analyze":
            query_history = args.get("query_history", [])
            all_query_data = json.dumps(query_history, ensure_ascii=False, default=str)
            last_result = query_history[-1].get("result", []) if query_history else []
            data_json = json.dumps(last_result, ensure_ascii=False, default=str)
            logger.info("Python analyze started: scenario_id=%s query_history=%d", self.scenario_id, len(query_history))
            return python_analyze(
                code=args.get("code", ""),
                data_json=data_json,
                all_query_data=all_query_data,
            )

        else:
            return {"error": f"未知工具: {name}"}
