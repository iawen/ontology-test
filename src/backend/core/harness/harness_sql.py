"""SQL harness for Data Query execution.

HarnessSQL validates and safely rewrites generated read-only SQL before it is
executed, so oversized detail queries are constrained before rows are fetched.
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from core.llm.chat_model import get_model_name, get_sync_client
from tools.logger import logger

DEFAULT_MAX_DETAIL_ROWS = 5000
DEFAULT_REWRITE_LIMIT = 1000

_FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|CALL|EXEC|GRANT|REVOKE|COPY|VACUUM|ANALYZE)\b",
    re.IGNORECASE,
)
_READONLY_RISK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bSELECT\s+.*\bINTO\b", re.IGNORECASE | re.DOTALL), "不允许 SELECT INTO"),
    (re.compile(r"\bFOR\s+UPDATE\b", re.IGNORECASE), "不允许 FOR UPDATE"),
)


@dataclass
class HarnessSQLResult:
    passed: bool
    sql: str
    original_sql: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    llm_actions: list[dict[str, str]] = field(default_factory=list)
    estimated_rows: int | None = None
    max_detail_rows: int = DEFAULT_MAX_DETAIL_ROWS
    rewrite_limit: int = DEFAULT_REWRITE_LIMIT

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "harness_sql_result",
            "passed": self.passed,
            "errors": self.errors,
            "warnings": self.warnings,
            "actions": self.actions,
            "llm_actions": self.llm_actions,
            "estimated_rows": self.estimated_rows,
            "max_detail_rows": self.max_detail_rows,
            "rewrite_limit": self.rewrite_limit,
            "original_sql": self.original_sql,
            "sql": self.sql,
        }


class HarnessSQL:
    """Validate and constrain generated SELECT/WITH SQL before execution."""

    def __init__(
        self,
        db_engine: Engine,
        *,
        max_detail_rows: int = DEFAULT_MAX_DETAIL_ROWS,
        rewrite_limit: int = DEFAULT_REWRITE_LIMIT,
        llm_client: Any | None = None,
        model_name: str | None = None,
    ):
        self.db_engine = db_engine
        self.max_detail_rows = max_detail_rows
        self.rewrite_limit = rewrite_limit
        self.llm_client = llm_client
        self.model_name = model_name

    def prepare(
        self,
        sql: str,
        *,
        user_question: str = "",
        schema_context: dict[str, Any] | None = None,
    ) -> HarnessSQLResult:
        original_sql = sql or ""
        normalized_sql = self._format_sql(self._strip_trailing_semicolon(original_sql.strip()))
        warnings: list[str] = []
        actions: list[str] = []
        llm_actions: list[dict[str, str]] = []
        logger.info(
            "HarnessSQL prepare started: sql_len=%d user_question_len=%d schema_context=%s",
            len(original_sql),
            len(user_question or ""),
            self._context_summary(schema_context),
        )
        logger.debug("HarnessSQL prepare original SQL: %s", self._sql_preview(original_sql))
        if normalized_sql != original_sql.strip():
            actions.append("format_sql")
            logger.info(
                "HarnessSQL formatted SQL: original_len=%d formatted_len=%d",
                len(original_sql.strip()),
                len(normalized_sql),
            )

        errors = self._validate_readonly_sql(normalized_sql)
        if errors:
            logger.warning(
                "HarnessSQL readonly validation failed: errors=%s sql=%s",
                errors,
                self._sql_preview(normalized_sql),
            )
            return HarnessSQLResult(
                passed=False,
                sql=normalized_sql,
                original_sql=original_sql,
                errors=errors,
                actions=actions,
                max_detail_rows=self.max_detail_rows,
                rewrite_limit=self.rewrite_limit,
            )
        logger.info("HarnessSQL readonly validation passed")

        syntax_errors = self._validate_syntax(normalized_sql)
        if syntax_errors:
            logger.warning("HarnessSQL syntax validation failed: errors=%s", syntax_errors)
            repaired_sql, repair_reason = self._llm_rewrite(
                mode="repair",
                sql=normalized_sql,
                user_question=user_question,
                schema_context=schema_context,
                error="; ".join(syntax_errors),
            )
            if repaired_sql:
                repaired_sql = self._format_sql(self._strip_trailing_semicolon(repaired_sql))
                repaired_errors = self._validate_readonly_sql(repaired_sql) + self._validate_syntax(repaired_sql)
                if not repaired_errors:
                    normalized_sql = repaired_sql
                    actions.append("llm_repair_syntax")
                    llm_actions.append({"action": "repair", "reason": repair_reason or "修复 SQL 语法错误"})
                    logger.info("HarnessSQL syntax repaired by LLM: reason=%s", repair_reason)
                    logger.debug("HarnessSQL syntax repaired SQL: %s", self._sql_preview(repaired_sql))
                else:
                    logger.warning("HarnessSQL LLM syntax repair rejected: errors=%s", repaired_errors)
                    syntax_errors.extend(repaired_errors)
            if syntax_errors and "llm_repair_syntax" not in actions:
                logger.warning("HarnessSQL prepare blocked by syntax validation: errors=%s", syntax_errors)
                return HarnessSQLResult(
                    passed=False,
                    sql=normalized_sql,
                    original_sql=original_sql,
                    errors=list(dict.fromkeys(syntax_errors)),
                    actions=actions,
                    llm_actions=llm_actions,
                    max_detail_rows=self.max_detail_rows,
                    rewrite_limit=self.rewrite_limit,
                )
        else:
            logger.info("HarnessSQL syntax validation passed")

        optimized_sql, optimize_reason = self._llm_rewrite(
            mode="optimize",
            sql=normalized_sql,
            user_question=user_question,
            schema_context=schema_context,
        )
        if optimized_sql:
            optimized_sql = self._format_sql(self._strip_trailing_semicolon(optimized_sql))
            optimized_errors = self._validate_readonly_sql(optimized_sql) + self._validate_syntax(optimized_sql)
            if optimized_errors:
                warnings.append("LLM 优化 SQL 未通过校验，已保留原 SQL：" + "; ".join(optimized_errors))
                logger.warning("HarnessSQL LLM optimization rejected: errors=%s", optimized_errors)
            elif optimized_sql != normalized_sql:
                normalized_sql = optimized_sql
                actions.append("llm_optimize_sql")
                llm_actions.append({"action": "optimize", "reason": optimize_reason or "根据用户问题优化 SQL"})
                logger.info("HarnessSQL SQL optimized by LLM: reason=%s", optimize_reason)
                logger.debug("HarnessSQL optimized SQL: %s", self._sql_preview(optimized_sql))

        estimated_rows = self._estimate_rows(normalized_sql)
        prepared_sql = normalized_sql

        # is_oversized = estimated_rows is not None and estimated_rows > self.max_detail_rows
        # if self._should_constrain(normalized_sql) and is_oversized:
        #     prepared_sql = self._wrap_with_limit(normalized_sql)
        #     warnings.append(
        #         f"SQL 预计返回 {estimated_rows} 行，超过查询结果阈值 {self.max_detail_rows}，"
        #         f"已改写为最多返回 {self.rewrite_limit} 行"
        #     )
        #     actions.append("limit_oversized_query")

        if prepared_sql != normalized_sql:
            logger.warning(
                "HarnessSQL rewrote query: estimated_rows=%s max_detail_rows=%s rewrite_limit=%s "
                "original_sql=%s rewritten_sql=%s",
                estimated_rows,
                self.max_detail_rows,
                self.rewrite_limit,
                normalized_sql,
                prepared_sql,
            )

        logger.info(
            "HarnessSQL prepare completed: passed=true actions=%s warnings=%d estimated_rows=%s final_sql_len=%d",
            actions,
            len(warnings),
            estimated_rows,
            len(prepared_sql),
        )
        return HarnessSQLResult(
            passed=True,
            sql=prepared_sql,
            original_sql=original_sql,
            warnings=warnings,
            actions=actions,
            llm_actions=llm_actions,
            estimated_rows=estimated_rows,
            max_detail_rows=self.max_detail_rows,
            rewrite_limit=self.rewrite_limit,
        )

    def repair_after_error(
        self,
        sql: str,
        error: str,
        *,
        user_question: str = "",
        schema_context: dict[str, Any] | None = None,
    ) -> HarnessSQLResult:
        logger.info(
            "HarnessSQL repair after execution error started: error=%s sql=%s",
            str(error)[:500],
            self._sql_preview(sql),
        )
        repaired_sql, repair_reason = self._llm_rewrite(
            mode="repair",
            sql=sql,
            user_question=user_question,
            schema_context=schema_context,
            error=error,
        )
        if not repaired_sql:
            logger.warning("HarnessSQL repair after execution error failed: LLM returned no SQL")
            return HarnessSQLResult(
                passed=False,
                sql=sql,
                original_sql=sql,
                errors=["LLM 未返回可用的修复 SQL"],
                actions=["llm_repair_failed"],
                max_detail_rows=self.max_detail_rows,
                rewrite_limit=self.rewrite_limit,
            )
        result = self.prepare(repaired_sql, user_question=user_question, schema_context=schema_context)
        result.original_sql = sql
        if result.passed:
            result.actions.insert(0, "llm_repair_execution_error")
            result.llm_actions.insert(0, {"action": "repair", "reason": repair_reason or "修复 SQL 执行错误"})
            logger.info("HarnessSQL repair after execution error passed: reason=%s", repair_reason)
        else:
            logger.warning("HarnessSQL repair after execution error rejected: errors=%s", result.errors)
        return result

    def _estimate_rows(self, sql: str) -> int | None:
        count_sql = f"SELECT COUNT(*) AS __harness_row_count FROM ({sql}) AS __harness_sql"
        try:
            started_at = time.time()
            with self.db_engine.connect() as conn:
                value = conn.execute(text(count_sql)).scalar()
            estimated_rows = int(value) if value is not None else None
            logger.info(
                "HarnessSQL row estimate completed: estimated_rows=%s duration_ms=%d",
                estimated_rows,
                int((time.time() - started_at) * 1000),
            )
            return estimated_rows
        except Exception as exc:
            logger.warning("HarnessSQL row estimate failed: error=%s sql=%s", str(exc), sql)
            return None

    def _validate_syntax(self, sql: str) -> list[str]:
        try:
            started_at = time.time()
            with self.db_engine.connect() as conn:
                conn.execute(text(f"EXPLAIN {sql}"))
            logger.debug(
                "HarnessSQL syntax validation completed: duration_ms=%d",
                int((time.time() - started_at) * 1000),
            )
            return []
        except Exception as exc:
            return [f"SQL 语法或执行计划校验失败: {exc}"]

    def _should_constrain(self, sql: str) -> bool:
        masked = self._mask_sql_literals_and_identifiers(sql)
        upper = masked.upper()
        return not re.search(r"\bLIMIT\s+\d+\b", upper)

    def _wrap_with_limit(self, sql: str) -> str:
        return f"SELECT * FROM ({sql}) AS __harness_limited LIMIT {self.rewrite_limit}"

    @staticmethod
    def _format_sql(sql: str) -> str:
        return "\n".join(line.rstrip() for line in (sql or "").strip().splitlines() if line.strip())

    def _validate_readonly_sql(self, sql: str) -> list[str]:
        masked_sql = self._mask_sql_literals_and_identifiers(sql)
        normalized = " ".join(sql.split())
        masked_normalized = " ".join(masked_sql.split())
        errors: list[str] = []

        if not normalized:
            errors.append("SQL 为空")
        if normalized and not normalized.upper().startswith(("SELECT ", "WITH ")):
            errors.append("只允许 SELECT/WITH 开头的只读查询")
        if self._has_non_trailing_statement_separator(masked_sql):
            errors.append("不允许多语句 SQL")
        forbidden = sorted({match.group(1).upper() for match in _FORBIDDEN_SQL_PATTERN.finditer(masked_sql)})
        if forbidden:
            errors.append(f"包含禁止关键字: {', '.join(forbidden)}")
        for pattern, message in _READONLY_RISK_PATTERNS:
            if pattern.search(masked_normalized):
                errors.append(message)
        return list(dict.fromkeys(errors))

    def _llm_rewrite(
        self,
        *,
        mode: str,
        sql: str,
        user_question: str = "",
        schema_context: dict[str, Any] | None = None,
        error: str = "",
    ) -> tuple[str, str]:
        if mode == "optimize" and not user_question.strip():
            logger.info("HarnessSQL LLM optimization skipped: user_question is empty")
            return "", ""
        client = self._get_llm_client()
        if client is None:
            logger.info("HarnessSQL LLM rewrite skipped: mode=%s reason=client_unavailable", mode)
            return "", ""

        prompt = self._build_llm_prompt(
            mode=mode,
            sql=sql,
            user_question=user_question,
            schema_context=schema_context,
            error=error,
        )
        try:
            logger.info(
                "HarnessSQL LLM rewrite started: mode=%s sql_len=%d user_question_len=%d",
                mode,
                len(sql or ""),
                len(user_question or ""),
            )
            response = client.chat.completions.create(
                model=self.model_name or get_model_name(),
                messages=[
                    {
                        "role": "system",
                        "content": "你是严格的 SQL Harness。只输出 JSON，不要输出解释文字或 Markdown。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
            )
            content = response.choices[0].message.content or ""
            payload = self._parse_llm_json(content)
            rewritten_sql = str(payload.get("sql") or "").strip()
            reason = str(payload.get("reason") or "").strip()
            logger.info(
                "HarnessSQL LLM rewrite completed: mode=%s returned_sql=%s reason_len=%d",
                mode,
                bool(rewritten_sql),
                len(reason),
            )
            return rewritten_sql, reason
        except Exception as exc:
            logger.warning("HarnessSQL LLM rewrite failed: mode=%s error=%s", mode, str(exc))
            return "", ""

    def _get_llm_client(self) -> Any | None:
        if self.llm_client is not None:
            logger.debug("HarnessSQL using injected LLM client")
            return self.llm_client
        if not os.getenv("OPENAI_API_KEY", "").strip():
            logger.info("HarnessSQL LLM client unavailable: OPENAI_API_KEY is empty")
            return None
        try:
            self.llm_client = get_sync_client()
            logger.info("HarnessSQL LLM client initialized")
        except Exception as exc:
            logger.warning("HarnessSQL LLM client unavailable: error=%s", str(exc))
            return None
        return self.llm_client

    @staticmethod
    def _sql_preview(sql: str, max_len: int = 1000) -> str:
        compact = " ".join((sql or "").split())
        return compact if len(compact) <= max_len else compact[:max_len] + "...[truncated]"

    @staticmethod
    def _context_summary(schema_context: dict[str, Any] | None) -> dict[str, Any]:
        if not schema_context:
            return {}
        return {
            "target_class": schema_context.get("target_class"),
            "metrics_count": len(schema_context.get("metrics") or []),
            "dimensions_count": len(schema_context.get("dimensions") or []),
            "filters_count": len(schema_context.get("filters") or []),
            "having_count": len(schema_context.get("having") or []),
            "metric_definitions_count": len(schema_context.get("metric_definitions") or []),
            "glossary_matches_count": len(schema_context.get("glossary_matches") or []),
            "locked_filter_values_count": len(schema_context.get("locked_filter_values") or []),
            "data_sources_count": len(schema_context.get("data_sources") or []),
        }

    @staticmethod
    def _build_llm_prompt(
        *,
        mode: str,
        sql: str,
        user_question: str,
        schema_context: dict[str, Any] | None,
        error: str,
    ) -> str:
        task = (
            "修复这个 SQL 的执行错误，尽量保持原查询意图和输出字段、条件里的值。"
            if mode == "repair"
            else "结合用户问题优化 SQL，删除不必要的 GROUP BY/维度/排序，避免返回过大的明细数据。"
        )
        return json.dumps(
            {
                "task": task,
                "rules": [
                    "只允许返回单条只读 SELECT/WITH SQL",
                    "不得新增、删除、修改数据库数据",
                    "如果原 SQL 已经合理，返回原 SQL",
                    "schema_context.metric_definitions 是指标口径定义，优化或修复时必须保持指标计算口径一致",
                    "schema_context.glossary_matches 是用户问题命中的术语/别名，应优先用于理解查询意图",
                    "schema_context.locked_filter_values 中的参数值已经过 EntityDisambiguatorAgent "
                    "消岐或类型归一化，禁止改动这些字段值",
                    "不要为了省事加过小 LIMIT；优先改写聚合粒度和不必要维度",
                    '输出 JSON: {"sql": "...", "reason": "..."}',
                ],
                "user_question": user_question,
                "error": error,
                "schema_context": schema_context or {},
                "sql": sql,
            },
            ensure_ascii=False,
            default=str,
        )

    @staticmethod
    def _parse_llm_json(content: str) -> dict[str, Any]:
        text_content = (content or "").strip()
        fence = re.fullmatch(r"```(?:json)?\s*\n(?P<body>[\s\S]*?)\n```", text_content, re.IGNORECASE)
        if fence:
            text_content = fence.group("body").strip()
        try:
            parsed = json.loads(text_content)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _strip_trailing_semicolon(sql: str) -> str:
        stripped = sql.strip()
        while stripped.endswith(";"):
            stripped = stripped[:-1].strip()
        return stripped

    @staticmethod
    def _has_non_trailing_statement_separator(masked_sql: str) -> bool:
        first_semicolon = masked_sql.find(";")
        if first_semicolon == -1:
            return False
        return bool(masked_sql[first_semicolon:].strip("; \t\r\n"))

    @classmethod
    def _mask_sql_literals_and_identifiers(cls, sql: str) -> str:
        masked: list[str] = []
        index = 0
        while index < len(sql):
            char = sql[index]
            if char in ("'", '"'):
                index = cls._consume_quoted(sql, index, char, masked)
                continue
            if char == "-" and index + 1 < len(sql) and sql[index + 1] == "-":
                next_line = sql.find("\n", index + 2)
                index = len(sql) if next_line == -1 else next_line
                masked.append(" ")
                continue
            if char == "/" and index + 1 < len(sql) and sql[index + 1] == "*":
                end = sql.find("*/", index + 2)
                index = len(sql) if end == -1 else end + 2
                masked.append(" ")
                continue
            masked.append(char)
            index += 1
        return "".join(masked)

    @staticmethod
    def _consume_quoted(sql: str, index: int, quote: str, masked: list[str]) -> int:
        masked.append(quote + quote)
        index += 1
        while index < len(sql):
            char = sql[index]
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                return index + 1
            if char == "\\" and quote == '"' and index + 1 < len(sql):
                index += 2
                continue
            index += 1
        return index
