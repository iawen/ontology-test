"""
专用名称（Glossary）管理 API
=============================
按场景 CRUD，供管理后台和 /api/chat 使用。
"""

import json

from fastapi import APIRouter, HTTPException

from core.db.db import get_db
from core.models.models import GlossaryTermCreate, GlossaryTermUpdate

router = APIRouter()


def _aliases(value) -> list[str]:
    if isinstance(value, list):
        return [str(alias).strip() for alias in value if str(alias).strip()]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        parsed = [value]
    return _aliases(parsed) if isinstance(parsed, list) else [str(parsed).strip()]


def _term_id(term_id: str) -> int:
    try:
        return int(term_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "术语 ID 无效") from exc


# ============================================================
# CRUD
# ============================================================

@router.get("/api/scenarios/{scenario_id}/glossary")
@router.get("/api/admin/scenarios/{scenario_id}/glossary", include_in_schema=False)
async def list_glossary(scenario_id: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM glossary_terms WHERE scenario_id=? ORDER BY sort_order, category",
        (scenario_id,)
    ).fetchall()
    conn.close()
    return [dict(row) | {"aliases": _aliases(row["aliases"])} for row in rows]


@router.post("/api/scenarios/{scenario_id}/glossary")
@router.post("/api/admin/scenarios/{scenario_id}/glossary", include_in_schema=False)
async def create_glossary_term(scenario_id: str, req: GlossaryTermCreate):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO glossary_terms
               (scenario_id, term, standard_name, aliases, description, category, sort_order)
               VALUES (?,?,?,?,?,?,?)""",
            (scenario_id, req.term, req.standard_name,
             json.dumps(req.aliases, ensure_ascii=False),
             req.description, req.category, req.sort_order)
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    return {"status": "ok"}


@router.put("/api/scenarios/{scenario_id}/glossary/{term_id}")
@router.put("/api/admin/scenarios/{scenario_id}/glossary/{term_id}", include_in_schema=False)
async def update_glossary_term(scenario_id: str, term_id: str, req: GlossaryTermUpdate):
    if not req.term.strip():
        raise HTTPException(400, "术语必填")
    conn = get_db()
    cursor = conn.execute(
        """UPDATE glossary_terms
           SET term=?, standard_name=?, aliases=?, description=?, category=?, sort_order=?
           WHERE id=? AND scenario_id=?""",
        (
            req.term.strip(), req.standard_name, json.dumps(_aliases(req.aliases), ensure_ascii=False),
            req.description, req.category, req.sort_order or 0, _term_id(term_id), scenario_id,
        ),
    )
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        raise HTTPException(404, "术语不存在")
    return {"status": "ok"}


@router.delete("/api/scenarios/{scenario_id}/glossary/{term_id}")
@router.delete("/api/admin/scenarios/{scenario_id}/glossary/{term_id}", include_in_schema=False)
async def delete_glossary_term(scenario_id: str, term_id: str):
    conn = get_db()
    cursor = conn.execute("DELETE FROM glossary_terms WHERE id=? AND scenario_id=?", (_term_id(term_id), scenario_id))
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        raise HTTPException(404, "术语不存在")
    return {"status": "ok"}


# ============================================================
# 供 /api/chat 调用的查询函数
# ============================================================

def get_glossary_for_prompt(scenario_id: str) -> str:
    """
    生成给 LLM system prompt 用的专用名称说明文本。
    格式：
      - 炒货 → 标准名：坚果炒货（别名：坚果、干果、零食）— 薛记的核心品类...
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM glossary_terms WHERE scenario_id=? ORDER BY sort_order",
        (scenario_id,)
    ).fetchall()
    conn.close()

    if not rows:
        return ""

    lines = []
    for r in rows:
        aliases = _aliases(r["aliases"])
        alias_str = f"（别名：{'、'.join(aliases)}）" if aliases else ""
        desc = f" — {r['description']}" if r["description"] else ""
        std = f"标准名：{r['standard_name']}" if r["standard_name"] else r["term"]
        lines.append(f"  - {r['term']} → {std}{alias_str}{desc}")

    return "\n".join(lines)


