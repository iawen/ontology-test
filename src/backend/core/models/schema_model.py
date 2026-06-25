"""
Schema Optimizer v3 - Pydantic 模型定义
========================================
定义 LLM 输出的结构化 schema，用于：
  1. JSON 输出验证（Pydantic 自动校验）
  2. 自校正重试（校验失败时反馈错误给 LLM 重试）
  3. 类型安全（下游代码可依赖类型）
"""

from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


class FieldOptimization(BaseModel):
    """字段优化建议"""
    physical_name: str = Field(..., description="物理列名（必须与数据库一致）")
    name: str = Field("", description="字段业务逻辑中文名")
    description: str = Field("", description="字段业务含义描述")
    type: str = Field("text", description="字段类型: text/numeric/date/boolean")
    is_primary_key: bool = Field(False, description="是否主键")
    is_foreign_key: bool = Field(False, description="是否外键")

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        allowed = {"text", "numeric", "date", "boolean"}
        if v not in allowed:
            return "text"
        return v


class ClassOptimization(BaseModel):
    """Class 优化建议"""
    id: str = Field(..., description="实体类ID（PascalCase）")
    name_cn: str = Field("", description="中文逻辑名称")
    description: str = Field("", description="实体业务定义与边界描述")
    primary_key: str = Field("", description="主键物理列名")
    csv_file: str = Field("", description="数据源文件名或表名")
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
    calculation: str = Field("", description="计算逻辑自然语言描述")
    formula: str = Field("", description="可执行SQL聚合公式，如 SUM(total_amount)")
    dimensions: List[str] = Field(default_factory=list, description="可用分析维度物理列名列表")
    required_dimensions: List[str] = Field(default_factory=list, description="最低必要粒度维度字段")
    filters_hint: str = Field("", description="过滤条件提示")
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
        if v not in allowed:
            return "bar"
        return v


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
        if v not in allowed:
            return "entity"
        return v


class OptimizationBatchResult(BaseModel):
    """单批次优化结果"""
    classes: List[ClassOptimization] = Field(default_factory=list)
    relationships: List[RelationshipOptimization] = Field(default_factory=list)
    metrics: List[MetricOptimization] = Field(default_factory=list)
    concepts: List[ConceptOptimization] = Field(default_factory=list)
    summary: str = Field("", description="本批次优化摘要")


class GlobalCorrectionResult(BaseModel):
    """全局校正结果"""
    class_renames: List[dict] = Field(default_factory=list, description="类ID重命名映射")
    relationship_corrections: List[RelationshipOptimization] = Field(default_factory=list)
    metric_corrections: List[MetricOptimization] = Field(default_factory=list)
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
