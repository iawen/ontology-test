"""
系统设置（System Settings）管理 API
=============================
管理 LLM 配置、提取参数等系统级设置。
设置以 key-value 形式存储在 system_settings 表中。
"""

import json

from fastapi import APIRouter, HTTPException

from core.db.db import get_db
from configs.global_config import Cfg
from core.models.models import SystemSettingsUpdate

router = APIRouter()

# 设置键列表（用于校验和默认值）
SETTING_KEYS = {
    "llm_provider": "openai",
    "llm_model": "qwen-plus",
    "llm_api_key": "",
    "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "extraction_batch_size": "5",
    "max_concurrent_extractions": "2",
    "auto_extract_on_upload": "true",
    "log_level": "INFO",
}


@router.get("/api/admin/settings")
async def get_settings():
    """获取系统设置，返回结构化对象"""
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM system_settings").fetchall()
    conn.close()

    # 从 key-value 行构建结构化对象
    settings = dict(SETTING_KEYS)  # 先填默认值
    for r in rows:
        if r["key"] in settings:
            settings[r["key"]] = r["value"]

    # 类型转换
    result = {
        "llm_provider": settings["llm_provider"],
        "llm_model": settings["llm_model"],
        "llm_api_key": settings["llm_api_key"],
        "llm_base_url": settings["llm_base_url"],
        "extraction_batch_size": int(settings["extraction_batch_size"]),
        "max_concurrent_extractions": int(settings["max_concurrent_extractions"]),
        "auto_extract_on_upload": settings["auto_extract_on_upload"].lower() == "true",
        "log_level": settings["log_level"],
    }
    return result


@router.put("/api/admin/settings")
async def update_settings(req: SystemSettingsUpdate):
    """更新系统设置"""
    conn = get_db()

    updates = {}
    if req.llm_provider:
        updates["llm_provider"] = req.llm_provider
    if req.llm_model:
        updates["llm_model"] = req.llm_model
    if req.llm_api_key:
        updates["llm_api_key"] = req.llm_api_key
    if req.llm_base_url:
        updates["llm_base_url"] = req.llm_base_url
    if req.extraction_batch_size is not None:
        updates["extraction_batch_size"] = str(req.extraction_batch_size)
    if req.max_concurrent_extractions is not None:
        updates["max_concurrent_extractions"] = str(req.max_concurrent_extractions)
    if req.auto_extract_on_upload is not None:
        updates["auto_extract_on_upload"] = "true" if req.auto_extract_on_upload else "false"
    if req.log_level:
        updates["log_level"] = req.log_level

    for key, value in updates.items():
        conn.execute(
            "INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    conn.commit()
    conn.close()

    # 如果更新了 LLM 配置，同步到 Cfg
    if any(k.startswith("llm_") for k in updates):
        _sync_llm_config()

    return {"status": "ok"}


@router.post("/api/admin/settings/test_connection")
async def test_llm_connection(body: dict = None):
    """测试 LLM 连接是否正常"""
    try:
        from openai import OpenAI
        # 读取当前设置
        conn = get_db()
        rows = conn.execute("SELECT key, value FROM system_settings WHERE key LIKE 'llm_%'").fetchall()
        conn.close()

        settings = {r["key"]: r["value"] for r in rows}
        api_key = settings.get("llm_api_key", Cfg.openai_api_key)
        base_url = settings.get("llm_base_url", Cfg.openai_base_url)
        model = settings.get("llm_model", Cfg.model_name)

        if not api_key or api_key == "sk-placeholder":
            raise HTTPException(400, "API Key 未配置")

        client = OpenAI(api_key=api_key, base_url=base_url)
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5,
        )
        return {"status": "ok", "model": model, "response": response.choices[0].message.content}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"连接测试失败: {str(e)}")


def _sync_llm_config():
    """将数据库中的 LLM 设置同步到 Cfg"""
    try:
        conn = get_db()
        rows = conn.execute("SELECT key, value FROM system_settings WHERE key LIKE 'llm_%'").fetchall()
        conn.close()

        for r in rows:
            if r["key"] == "llm_api_key" and r["value"]:
                Cfg.openai_api_key = r["value"]
            elif r["key"] == "llm_base_url" and r["value"]:
                Cfg.openai_base_url = r["value"]
            elif r["key"] == "llm_model" and r["value"]:
                Cfg.model_name = r["value"]
    except Exception:
        pass


def get_setting(key: str, default: str = "") -> str:
    """获取单个设置值"""
    conn = get_db()
    row = conn.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def get_int_setting(key: str, default: int = 0) -> int:
    """获取整数设置值"""
    val = get_setting(key, str(default))
    try:
        return int(val)
    except ValueError:
        return default


def get_bool_setting(key: str, default: bool = False) -> bool:
    """获取布尔设置值"""
    val = get_setting(key, "true" if default else "false")
    return val.lower() == "true"
