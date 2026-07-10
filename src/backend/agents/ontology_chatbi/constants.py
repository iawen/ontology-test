"""Shared constants for the data query agent package."""

import re


TOOL_PURPOSES = {
    "query_ontology_data": "按指标、维度和筛选条件查询业务数据。",
    "python_analyze": "基于查询结果做进一步统计、计算或归纳分析。",
}
TOOL_DISPLAY_NAMES = {
    "query_ontology_data": "多维模型高阶查询",
    "python_analyze": "深度策略分析(Python)",
}

SSE_CACHE_TTL_SECONDS = 24 * 60 * 60
DIRECT_ANSWER_MAX_ROWS = 100
DIRECT_ANSWER_MAX_JSON_CHARS = 24000
FINAL_AFTER_TOOL_MAX_ROWS = 100
FINAL_ANSWER_SAMPLE_HEAD_ROWS = 30
FINAL_ANSWER_SAMPLE_TAIL_ROWS = 5
FINAL_ANSWER_MAX_CONTEXT_CHARS = 24000

COMPARISON_QUERY_KEYWORDS = (
    "相比",
    "对比",
    "比较",
    "变化",
    "变动",
    "环比",
    "同比",
    "上月",
    "上个月",
    "上周",
    "去年",
    "上年",
    "previous",
    "last month",
    "mom",
    "yoy",
)
COMPARISON_EVIDENCE_KEYWORDS = (
    "相比",
    "对比",
    "变化",
    "变动",
    "差值",
    "增幅",
    "增长",
    "下降",
    "环比",
    "同比",
    "上月",
    "上个月",
    "去年",
    "上年",
    "previous",
    "last",
    "mom",
    "yoy",
)
TIME_DIMENSION_KEYWORDS = (
    "日期",
    "时间",
    "月份",
    "年月",
    "年度",
    "年份",
    "季度",
    "周",
    "ap",
    "month",
    "year",
)
RATIO_QUERY_KEYWORDS = ("占比", "比例", "贡献", "贡献率", "份额", "占率", "share", "ratio", "percentage")
RATIO_EVIDENCE_KEYWORDS = (
    "占比",
    "比例",
    "贡献",
    "贡献率",
    "份额",
    "占率",
    "%",
    "share",
    "ratio",
    "percentage",
)
CAUSE_QUERY_KEYWORDS = ("为什么", "原因", "归因", "驱动", "影响因素", "导致", "why", "reason")
PYTHON_ANALYZE_COMPLEX_PATTERNS = (
    "pivot",
    "merge",
    "join",
    "corr",
    "regression",
    "rolling",
    "groupby",
    "apply",
    "lambda",
    "同比",
    "环比",
    "相关",
    "回归",
    "预测",
)

PROGRESS_QUERY_KEYWORDS = {
    "进度",
    "完成",
    "完成情况",
    "达成",
    "达成率",
    "目标",
    "mtd",
    "daily",
    "tth",
}
PROGRESS_METRIC_KEYWORDS = {
    "mtd",
    "月至今",
    "月度",
    "当月",
    "累计",
    "进度",
    "达成",
    "达成率",
    "工作日",
    "实际销售",
    "目标销售",
    "销售额",
    "上月",
    "环比",
    "actual",
    "target",
    "workday",
}
QUARTER_VALUE_PATTERN = re.compile(r"^\d{4}Q[1-4]$", re.IGNORECASE)
BARE_QUARTER_VALUE_PATTERN = re.compile(r"^Q[1-4]$", re.IGNORECASE)
CHINESE_QUARTER_VALUE_PATTERN = re.compile(r"^(?:(?P<year>\d{4})年?)?第?(?P<quarter>[一二三四1-4])(?:季度|季)$")


FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|MERGE|GRANT|REVOKE|CALL|EXEC|EXECUTE|COPY|"
    r"REPLACE|UPSERT|VACUUM|ANALYZE|ATTACH|DETACH|PRAGMA|SET|RESET|USE|LOAD|UNLOAD|LOCK|UNLOCK|"
    r"COMMENT|RENAME|BACKUP|RESTORE|IMPORT|EXPORT|BEGIN|COMMIT|ROLLBACK|SAVEPOINT)\b",
    re.IGNORECASE,
)
DOLLAR_QUOTE_START_PATTERN = re.compile(r"\$[A-Za-z_]\w*\$|\$\$")
READONLY_RISK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bSELECT\b[\s\S]*\bINTO\b", re.IGNORECASE), "不允许 SELECT INTO"),
    (re.compile(r"\bFOR\s+(?:NO\s+KEY\s+)?UPDATE\b", re.IGNORECASE), "不允许 SELECT ... FOR UPDATE"),
    (re.compile(r"\bFOR\s+(?:KEY\s+)?SHARE\b", re.IGNORECASE), "不允许 SELECT ... FOR SHARE"),
    (re.compile(r"\bLOCK\s+IN\s+SHARE\s+MODE\b", re.IGNORECASE), "不允许锁定读查询"),
    (
        re.compile(r"\b(NEXTVAL|SETVAL|PG_ADVISORY_LOCK|PG_ADVISORY_XACT_LOCK)\s*\(", re.IGNORECASE),
        "不允许调用可能产生副作用的函数",
    ),
)
