"""
On Budget AI - 本体驱动后端 v3
================================
修复重点：
  1. LLM 不调用工具时，自动重试并强制 tool_choice
  2. 添加详细日志，方便排查
  3. 兼容不同 OpenAI 兼容模型
  4. 补全管理后台所需的所有 API 接口
"""

import uvicorn
import hashlib

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from configs.global_config import Cfg
from core.models.models import LoginReq
from core.db.db import get_db, init_db
from prompts.prompt import get_engine
from tools.helpers import create_token


from agents.ontology_chatbi.views import router as chat_router

from modules.scenarios import router as scenarios_router
from modules.knowledge_files import router as kg_router
from modules.schema import router as schema_router
from modules.conversations import router as conversations_router
from modules.metrics import router as metrics_router
from modules.glossary import router as glossary_router
from modules.skills import router as skills_router
from modules.users import router as users_router
from modules.chart_rules import router as chart_rules_router
from modules.extraction_logs import router as extraction_logs_router
from modules.audit_logs import router as audit_logs_router
from modules.settings import router as settings_router
from modules.dashboard import router as dashboard_router
from modules.data_connections import router as data_connections_router
from modules.actions import router as actions_router
from modules.alert_rules import router as alert_rules_router
from modules.workflow_engine import router as workflow_router
from modules.schema_optimization import router as schema_optimizer_router

# 初始化数据库
init_db()

app = FastAPI(title="On Budget AI - Ontology Driven", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/admin/login")
async def login(req: LoginReq):
    conn = get_db()
    pwd_hash = hashlib.sha256(req.password.encode()).hexdigest()
    cur = conn.execute("SELECT * FROM users WHERE username=? AND password_hash=?", (req.username, pwd_hash))
    user = cur.fetchone()
    conn.close()
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    return {"token": create_token(req.username, Cfg.jwt_secret, user["role"]), "username": req.username, "role": user["role"]}


@app.get("/api/health")
async def health():
    engine = get_engine()
    return {
        "status": "ok",
        "model": Cfg.model_name,
        "classes": len(engine.list_classes()) if engine else 0,
    }


# 注册路由
app.include_router(dashboard_router, tags=["Dashboard"])
app.include_router(scenarios_router, tags=["Scenarios"])
app.include_router(kg_router, tags=["Data Manager"])
app.include_router(schema_router, tags=["Schema Manager"])
app.include_router(chat_router, tags=["Chat"])
app.include_router(conversations_router, tags=["Conversations"])
app.include_router(metrics_router, tags=["Metrics"])
app.include_router(glossary_router, tags=["Glossary"])
app.include_router(skills_router, tags=["Skills"])
app.include_router(users_router, tags=["Users"])
app.include_router(chart_rules_router, tags=["Chart Rules"])
app.include_router(extraction_logs_router, tags=["Extraction Logs"])
app.include_router(audit_logs_router, tags=["Audit Logs"])
app.include_router(settings_router, tags=["Settings"])
app.include_router(data_connections_router, tags=["Data Connections"])
app.include_router(actions_router, tags=["Actions"])
app.include_router(alert_rules_router, tags=["Alert Rules"])
app.include_router(workflow_router, tags=["Workflow"])
app.include_router(schema_optimizer_router, tags=["Schema Optimization"])

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
