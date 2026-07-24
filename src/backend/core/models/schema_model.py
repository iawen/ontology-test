"""
Schema Optimizer v3.1 - Pydantic 模型定义
==========================================
新增：质量评估模型 (QualityAssessmentResult)
"""

from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


class FieldOptimization(BaseModel):
    """字段优化建议"""
    name: str = Field(..., description="物理列名（必须与数据库一致）")
    name_cn: str = Field("", description="字段业务逻辑中文名")
    description: str = Field("", description="字段业务含义描述")
    type: str = Field("text", description="字段类型: text/numeric/date/boolean")
    is_primary_key: bool = Field(False, description="是否主键")
    is_foreign_key: bool = Field(False, description="是否外键")

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        allowed = {"text", "numeric", "date", "boolean"}
        return v if v in allowed else "text"


class ClassOptimization(BaseModel):
    """Class 优化建议"""
    id: str = Field(..., description="实体类ID（PascalCase）")
    name_cn: str = Field("", description="中文逻辑名称")
    description: str = Field("", description="实体业务定义与边界描述")
    primary_key: str = Field("", description="主键物理列名")
    table_name: str = Field("", description="数据源文件名或表名")
    fields: List[FieldOptimization] = Field(default_factory=list, description="字段列表（可只包含需优化的字段）")

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not v or not v.strip():
            raise ValueError("class id 不能为空")
        return v.strip()


class RelationshipOptimization(BaseModel):
    """关系优化建议"""
    source: str = Field(..., description="源实体类ID")
    target: str = Field(..., description="目标实体类ID")
    type: str = Field("belongs_to", description="关系类型")
    source_key: str = Field("", description="源端关联键")
    target_key: str = Field("", description="目标端关联键")
    join_key: str = Field("", description="兼容旧版的统一关联键")

    @field_validator("source", "target")
    @classmethod
    def validate_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("source/target 不能为空")
        return v.strip()


class MetricOptimization(BaseModel):
    """指标优化建议"""
    id: str = Field(..., description="指标ID（下划线英文）")
    name: str = Field("", description="指标逻辑中文名称")
    description: str = Field("", description="指标业务定义及应用场景")
    category: str = Field("", description="指标业务分类")
    target_class: str = Field("", description="绑定的实体类ID")
    definition: dict = Field(default_factory=dict, description="结构化指标定义")
    dimensions: List[str] = Field(default_factory=list, description="可用分析维度物理列名列表")
    required_dimensions: List[str] = Field(default_factory=list, description="最低必要粒度维度字段")
    dimension_group_ids: List[str] = Field(default_factory=list, description="关联的分析维度组ID")
    chart_type: str = Field("bar", description="推荐图表类型")

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not v or not v.strip():
            raise ValueError("metric id 不能为空")
        return v.strip()

    @field_validator("chart_type")
    @classmethod
    def validate_chart_type(cls, v):
        allowed = {"bar", "line", "pie", "table", "gauge", "scatter", "area"}
        return v if v in allowed else "bar"


class ConceptOptimization(BaseModel):
    """概念优化建议"""
    id: str = Field(..., description="概念ID")
    name: str = Field("", description="概念名称")
    description: str = Field("", description="概念描述")
    parent_id: str = Field("", description="父概念ID")
    level: int = Field(0, description="层级")
    concept_type: str = Field("entity", description="概念类型")
    related_class: str = Field("", description="关联实体类ID")

    @field_validator("concept_type")
    @classmethod
    def validate_concept_type(cls, v):
        allowed = {"subject_domain", "dimension_group", "fact_group", "entity", "kpi", "category"}
        return v if v in allowed else "entity"


class DimensionGroupOptimization(BaseModel):
    """分析维度组优化建议。字段映射使用已存在的 Class 与逻辑字段。"""
    id: str = Field(..., description="稳定维度组ID")
    name: str = Field("", description="业务名称")
    description: str = Field("", description="业务说明")
    group_type: str = Field("categorical", description="time/categorical/hierarchy")
    is_required: bool = Field(False, description="缺失时是否需要澄清")
    default_option: str = Field("", description="默认选项值")
    clarification_policy: str = Field("ask_when_ambiguous", description="auto_fill/ask_when_ambiguous/always_ask")
    options: List[dict] = Field(default_factory=list)
    field_mappings: List[dict] = Field(default_factory=list)

    @field_validator("group_type")
    @classmethod
    def validate_group_type(cls, value):
        return value if value in {"time", "categorical", "hierarchy"} else "categorical"


class OptimizationBatchResult(BaseModel):
    """单批次优化结果"""
    classes: List[ClassOptimization] = Field(default_factory=list)
    relationships: List[RelationshipOptimization] = Field(default_factory=list)
    metrics: List[MetricOptimization] = Field(default_factory=list)
    dimension_groups: List[DimensionGroupOptimization] = Field(default_factory=list)
    concepts: List[ConceptOptimization] = Field(default_factory=list)
    summary: str = Field("", description="本批次优化摘要")


class GlobalCorrectionResult(BaseModel):
    """全局校正结果（扩展版）"""
    class_renames: List[dict] = Field(default_factory=list, description="类ID重命名映射")
    relationship_corrections: List[RelationshipOptimization] = Field(default_factory=list)
    metric_corrections: List[MetricOptimization] = Field(default_factory=list)
    concept_corrections: List[ConceptOptimization] = Field(default_factory=list, description="概念树修正")
    metric_consistency_warnings: List[str] = Field(default_factory=list, description="指标口径不一致警告")
    concept_tree_warnings: List[str] = Field(default_factory=list, description="概念树结构警告")
    summary: str = Field("", description="全局校正摘要")


class OptimizationDiff(BaseModel):
    """优化差异报告（用于审计）"""
    added_classes: List[str] = Field(default_factory=list)
    modified_classes: List[str] = Field(default_factory=list)
    added_metrics: List[str] = Field(default_factory=list)
    modified_metrics: List[str] = Field(default_factory=list)
    added_relationships: List[str] = Field(default_factory=list)
    added_concepts: List[str] = Field(default_factory=list)
    summary: str = Field("")


class QualityAssessmentResult(BaseModel):
    """质量评估结果（LLM-as-Judge）"""
    overall_score: float = Field(..., ge=0, le=10, description="综合质量评分 0-10")
    confidence: float = Field(..., ge=0, le=1, description="置信度 0-1")
    strengths: List[str] = Field(default_factory=list, description="优化亮点")
    weaknesses: List[str] = Field(default_factory=list, description="需改进之处")
    high_risk_items: List[str] = Field(default_factory=list, description="需重点人工审核项")
    summary: str = Field("", description="评估总结")