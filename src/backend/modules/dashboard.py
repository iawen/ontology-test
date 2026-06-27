"""
仪表盘（Dashboard）API
=============================
提供系统概览统计数据。
"""

from fastapi import APIRouter

from core.db.db import get_db

router = APIRouter()


@router.get("/api/admin/dashboard")
async def get_dashboard():
    """获取仪表盘统计数据"""
    conn = get_db()

    # 场景统计
    total_scenarios = conn.execute("SELECT COUNT(*) FROM scenarios").fetchone()[0]
    active_scenarios = conn.execute("SELECT COUNT(*) FROM scenarios WHERE is_active=1").fetchone()[0]

    # 文件统计
    total_files = 0
    try:
        import os
        from configs.global_config import Cfg
        scenarios_root = Cfg.scenarios_root
        if os.path.exists(scenarios_root):
            for scenario_dir in os.listdir(scenarios_root):
                data_dir = os.path.join(scenarios_root, scenario_dir, "data")
                if os.path.isdir(data_dir):
                    total_files += len([f for f in os.listdir(data_dir) if f.endswith('.csv')])
    except Exception:
        pass

    # Schema 类统计
    total_schema_classes = conn.execute("SELECT COUNT(*) FROM schema_classes").fetchone()[0]

    # 指标统计
    total_metrics = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]

    # 概念统计
    total_concepts = conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]

    # 术语统计
    total_glossary_terms = conn.execute("SELECT COUNT(*) FROM glossary_terms").fetchone()[0]

    # 技能统计
    total_skills = conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]

    # 最近提取日志
    recent_extractions = []
    try:
        rows = conn.execute(
            "SELECT * FROM extraction_logs ORDER BY started_at DESC LIMIT 10"
        ).fetchall()
        recent_extractions = [dict(r) for r in rows]
    except Exception:
        pass  # extraction_logs 表可能还不存在

    conn.close()

    return {
        "total_scenarios": total_scenarios,
        "active_scenarios": active_scenarios,
        "total_files": total_files,
        "total_schema_classes": total_schema_classes,
        "total_metrics": total_metrics,
        "total_concepts": total_concepts,
        "total_glossary_terms": total_glossary_terms,
        "total_skills": total_skills,
        "recent_extractions": recent_extractions,
    }
