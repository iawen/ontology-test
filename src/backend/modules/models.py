
from pydantic import BaseModel
from typing import Optional


class LoginReq(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    scenario_id: str
    conversation_id: str
    messages: list[dict]


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


class SchemaRelationEdit(BaseModel):
    source: str
    target: str
    type: str = ""
    join_key: str = ""


# ============================================================
# 指标
# ============================================================

class MetricCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = ""
    target_class: str = ""
    calculation: str = ""
    formula: str = ""
    dimensions: list[str] = []
    required_dimensions: list[str] = []
    filters_hint: str = ""
    chart_type: str = "bar"
    sort_order: int = 0


class MetricUpdate(BaseModel):
    name: str = ""
    description: str = ""
    category: str = ""
    target_class: str = ""
    calculation: str = ""
    formula: str = ""
    dimensions: list[str] | None = None
    required_dimensions: list[str] | None = None
    filters_hint: str = ""
    chart_type: str = ""
    sort_order: int | None = None


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


class ConceptUpdate(BaseModel):
    name: str = ""
    description: str = ""
    parent_id: str = ""
    level: int | None = None
    concept_type: str = ""
    related_class: str = ""
    sort_order: int | None = None


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
# 提取日志
# ============================================================

class ExtractionLogCreate(BaseModel):
    scenario_id: str
    type: str
    trigger: str = "manual"


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
