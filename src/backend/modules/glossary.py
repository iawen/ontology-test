"""
专用名称（Glossary）管理 API
=============================
按场景 CRUD，供管理后台和 /api/chat 使用。
"""

import json

from fastapi import APIRouter, HTTPException

from tools.db import get_db
from modules.models import GlossaryTermCreate, GlossaryTermUpdate

router = APIRouter()


# ============================================================
# CRUD
# ============================================================

@router.get("/api/admin/scenarios/{scenario_id}/glossary")
async def list_glossary(scenario_id: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM glossary_terms WHERE scenario_id=? ORDER BY sort_order, category",
        (scenario_id,)
    ).fetchall()
    conn.close()
    return [dict(r) | {"aliases": json.loads(r["aliases"])} for r in rows]


@router.post("/api/admin/scenarios/{scenario_id}/glossary")
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


@router.put("/api/admin/scenarios/{scenario_id}/glossary/{term_id}")
async def update_glossary_term(scenario_id: str, term_id: str, req: GlossaryTermUpdate):
    conn = get_db()
    sets, vals = [], []
    for k, v in [("term", req.term), ("standard_name", req.standard_name),
                  ("description", req.description), ("category", req.category),
                  ("sort_order", req.sort_order)]:
        if v is not None and v != "":
            sets.append(f"{k}=?")
            vals.append(v)
    if req.aliases is not None:
        sets.append("aliases=?")
        vals.append(json.dumps(req.aliases, ensure_ascii=False))
    if not sets:
        conn.close()
        return {"status": "ok"}
    vals.extend([int(term_id), scenario_id])
    conn.execute(
        f"UPDATE glossary_terms SET {','.join(sets)} WHERE id=? AND scenario_id=?",
        vals
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.delete("/api/admin/scenarios/{scenario_id}/glossary/{term_id}")
async def delete_glossary_term(scenario_id: str, term_id: str):
    conn = get_db()
    conn.execute("DELETE FROM glossary_terms WHERE id=? AND scenario_id=?", (int(term_id), scenario_id))
    conn.commit()
    conn.close()
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
        aliases = json.loads(r["aliases"]) if r["aliases"] else []
        alias_str = f"（别名：{'、'.join(aliases)}）" if aliases else ""
        desc = f" — {r['description']}" if r["description"] else ""
        std = f"标准名：{r['standard_name']}" if r["standard_name"] else r["term"]
        lines.append(f"  - {r['term']} → {std}{alias_str}{desc}")

    return "\n".join(lines)


def match_glossary_terms(scenario_id: str, user_message: str) -> list[dict]:
    """
    匹配用户消息中出现的专用名称，返回匹配到的条目列表。
    用于在 chat 流程中识别用户使用了哪些企业术语。
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM glossary_terms WHERE scenario_id=?",
        (scenario_id,)
    ).fetchall()
    conn.close()

    matched = []
    msg_lower = user_message.lower()
    for r in rows:
        aliases = json.loads(r["aliases"]) if r["aliases"] else []
        all_terms = [r["term"]] + aliases + ([r["standard_name"]] if r["standard_name"] else [])
        for t in all_terms:
            if t and t.lower() in msg_lower:
                matched.append({
                    "term": r["term"],
                    "standard_name": r["standard_name"],
                    "aliases": aliases,
                    "description": r["description"],
                    "category": r["category"],
                })
                break  # 一个条目只匹配一次
    return matched
