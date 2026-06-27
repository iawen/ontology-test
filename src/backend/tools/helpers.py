import os
import json
from datetime import datetime, timedelta
import jwt
from fastapi import HTTPException

from configs.global_config import Cfg


DATA_KEYWORDS = [
    "销售", "库存", "门店", "商品", "品类", "利润", "成本", "金额",
    "异常", "预警", "风险", "问题", "故障",
    "任务", "执行", "完成",
    "排名", "对比", "趋势", "多少", "统计", "数据",
    "情况", "怎么样", "如何", "有哪些", "查", "看",
    "门店", "品类", "品类", "商品",
]


def is_data_query(text: str) -> bool:
    """判断用户消息是否涉及数据查询"""
    return any(kw in text for kw in DATA_KEYWORDS)


def create_token(username: str, secret: str, role: str = "admin") -> str:
    payload = {"sub": username, "role": role, "exp": datetime.utcnow() + timedelta(hours=24)}
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_token(token, secret) -> dict:
    """验证 JWT token，支持传入 str 或 Request 对象"""
    from fastapi import Request
    if isinstance(token, Request):
        auth = token.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        else:
            raise HTTPException(401, "Missing token")
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")