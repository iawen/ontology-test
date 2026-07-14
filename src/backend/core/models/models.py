
from pydantic import BaseModel
from typing import Optional, Any
from typing import Any

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


ReviewStatus = str


class LoginReq(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    session_id: str
    agent_id: str
    query_id: str | None = None
    message: str
    language: str | None = None
    options: dict[str, Any] | None = None


# ============================================================
# 场景
# ============================================================

class ScenarioCreate(BaseModel):
    id: str
    name: str
    description: str = ""


class ScenarioUpdate(BaseModel):
    name: str = ""
    description: str = ""


# ============================================================
# Schema
# ============================================================

class SchemaClassEdit(BaseModel):
    id: str
    name_cn: str
    description: str = ""
    properties: list[str] = []
    fields: list[dict] = []
    csv_file: str = ""
    primary_key: str = ""
    # 管理端使用 -1 / 0 / 1 表示不通过、待审核、已通过；兼容旧客户端的 bool。
    is_reviewed: int | bool = 0
    review_status: ReviewStatus = "pending"


class SchemaRelationEdit(BaseModel):
    source: str
    target: str
    type: str = ""
    source_key: str = ""
    target_key: str = ""
    join_key: str = ""
    description: str = ""
    # 管理端使用 -1 / 0 / 1 表示不通过、待审核、已通过；兼容旧客户端的 bool。
    is_reviewed: int | bool = 0
    review_status: ReviewStatus = "pending"


class SchemaOptimizationRequest(BaseModel):
    file_ids: list[str] = []
    incremental: bool = True
    target_class_ids: list[str] | None = None
    enable_quality_assessment: bool = True


# ============================================================
# 指标
# ============================================================

class MetricCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = ""
    target_class: str = ""
    definition: dict = {}
    dimensions: list[str] = []
    required_dimensions: list[str] = []
    dimension_group_ids: list[str] = []
    chart_type: str = "bar"
    sort_order: int = 0
    is_reviewed: int | bool = 0
    review_status: ReviewStatus = "pending"


class MetricUpdate(BaseModel):
    name: str = ""
    description: str = ""
    category: str = ""
    target_class: str = ""
    definition: dict | None = None
    dimensions: list[str] | None = None
    required_dimensions: list[str] | None = None
    dimension_group_ids: list[str] | None = None
    chart_type: str = ""
    sort_order: int | None = None
    is_reviewed: int | bool | None = None
    review_status: ReviewStatus | None = None


class MetricBatchDelete(BaseModel):
    ids: list[str]


# ============================================================
# 分析维度组
# ============================================================

class DimensionFieldMapping(BaseModel):
    option_value: str
    class_id: str
    field_name: str
    display_name: str = ""
    priority: int = 0


class DimensionOption(BaseModel):
    value: str
    label: str
    aliases: list[str] = []
    is_default: bool = False
    sort_order: int = 0
    status: str = "approved"


class DimensionGroupCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    group_type: str = "categorical"
    concept_id: str = ""
    is_required: bool = False
    default_option: str = ""
    clarification_policy: str = "ask_when_ambiguous"
    status: str = "draft"
    options: list[DimensionOption] = []
    field_mappings: list[DimensionFieldMapping] = []
    metric_ids: list[str] = []


class DimensionGroupUpdate(BaseModel):
    name: str = ""
    description: str = ""
    group_type: str = ""
    concept_id: str = ""
    is_required: bool | None = None
    default_option: str = ""
    clarification_policy: str = ""
    status: str = ""
    options: list[DimensionOption] | None = None
    field_mappings: list[DimensionFieldMapping] | None = None
    metric_ids: list[str] | None = None


# ============================================================
# 概念
# ============================================================

class ConceptCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    parent_id: str = ""
    level: int = 0
    concept_type: str = "entity"
    related_class: str = ""
    sort_order: int = 0
    is_reviewed: bool = False
    review_status: ReviewStatus = "pending"


class ConceptUpdate(BaseModel):
    name: str = ""
    description: str = ""
    parent_id: str = ""
    level: int | None = None
    concept_type: str = ""
    related_class: str = ""
    sort_order: int | None = None
    is_reviewed: bool | None = None
    review_status: ReviewStatus | None = None


# ============================================================
# 图表规则
# ============================================================

class ChartRuleCreate(BaseModel):
    data_pattern: str
    chart_type: str
    description: str = ""
    priority: int = 0


class ChartRuleUpdate(BaseModel):
    data_pattern: str = ""
    chart_type: str = ""
    description: str = ""
    priority: int | None = None


# ============================================================
# 专用名称（Glossary）
# ============================================================

class GlossaryTermCreate(BaseModel):
    term: str
    standard_name: str = ""
    aliases: list[str] = []
    description: str = ""
    category: str = ""
    sort_order: int = 0


class GlossaryTermUpdate(BaseModel):
    term: str = ""
    standard_name: str = ""
    aliases: list[str] | None = None
    description: str = ""
    category: str = ""
    sort_order: int | None = None


# ============================================================
# 技能包（Skills）
# ============================================================

class SkillCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    trigger_condition: str = ""
    content: str = ""
    is_active: bool = True
    sort_order: int = 0


class SkillUpdate(BaseModel):
    name: str = ""
    description: str = ""
    trigger_condition: str = ""
    content: str = ""
    is_active: bool | None = None
    sort_order: int | None = None


# ============================================================
# 用户管理
# ============================================================

class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "admin"


class UserUpdate(BaseModel):
    username: str = ""
    password: str = ""
    role: str = ""


# ============================================================
# 系统设置
# ============================================================

class SystemSettingsUpdate(BaseModel):
    llm_provider: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    extraction_batch_size: int | None = None
    max_concurrent_extractions: int | None = None
    auto_extract_on_upload: bool | None = None
    log_level: str = ""


# ============================================================
# 审计日志
# ============================================================

class AuditLogCreate(BaseModel):
    user_id: int = 0
    username: str = ""
    action: str
    resource_type: str = ""
    resource_id: str = ""
    scenario_id: str = ""
    detail: str = ""
    ip: str = ""


# ============================================================
# 数据库连接
# ============================================================

class DataConnectionCreate(BaseModel):
    name: str
    db_type: str = "postgresql"  # postgresql / mysql
    connection_url: str


class DataConnectionUpdate(BaseModel):
    name: str = ""
    db_type: str = ""
    connection_url: str = ""
    is_active: bool | None = None


# ============================================================
# Action（行动）
# ============================================================

class ActionCreate(BaseModel):
    name: str
    description: str = ""
    action_type: str = "notification"  # notification / webhook / email / data_update / workflow
    trigger_condition: str = ""
    target_object: str = ""
    parameters: dict = {}
    requires_confirm: bool = True
    sort_order: int = 0


class ActionUpdate(BaseModel):
    name: str = ""
    description: str = ""
    action_type: str = ""
    trigger_condition: str = ""
    target_object: str = ""
    parameters: dict | None = None
    is_active: bool | None = None
    requires_confirm: bool | None = None
    sort_order: int | None = None


class ActionExecuteRequest(BaseModel):
    action_id: str
    scenario_id: str
    context: dict = {}  # 执行上下文，如查询结果、触发原因等
    confirmed: bool = False  # 是否已确认执行


# ============================================================
# 告警规则
# ============================================================

class AlertRuleCreate(BaseModel):
    name: str
    description: str = ""
    target_class: str
    condition_expression: str  # 如 "total_amount < 10000" 或 "anomaly_count > 5"
    action_id: str = ""
    severity: str = "warning"  # info / warning / critical


class AlertRuleUpdate(BaseModel):
    name: str = ""
    description: str = ""
    target_class: str = ""
    condition_expression: str = ""
    action_id: str = ""
    severity: str = ""
    is_active: bool | None = None


class DeepAgentMessage:
    id: Mapped[str] = mapped_column(Text, primary_key=True, comment="消息 ID。")
    session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("deep_agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属会话 ID。",
    )
    role: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="消息角色：user/assistant/system。",
    )
    query_id: Mapped[str | None] = mapped_column(Text, default=None, comment="本轮查询 ID。")
    content: Mapped[Any] = mapped_column(
        JSONB, nullable=True, default=None, comment="消息内容，支持字符串或结构化 JSON。"
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="completed",
        server_default="completed",
        comment="消息状态。",
    )

