
import os
from pathlib import Path
import socket
import httpx
import time
import tempfile
from urllib.parse import urlparse
from dotenv import load_dotenv  
from typing import AsyncIterator, Literal
from OpenSSL import SSL, crypto
from openai import AsyncOpenAI
from dotenv import load_dotenv

from openai import OpenAI, AsyncClient

load_dotenv()


class Cfg:
    base_dir = os.path.join(str(Path(__file__).resolve().parent.parent), "data")
    
    db_type = os.getenv("DB_TYPE", "sqlite3")
    db_dsn = os.getenv("DATABASE_URL", "sqlite:///" + os.path.join(base_dir, "admin.db"))
    db_path = os.path.join(base_dir, "admin.db")

    jwt_secret = os.getenv("JWT_SECRET", "on-budget-ai-admin-2026")
    scenarios_root = os.path.join(base_dir, "scenarios")
    openai_ssl = False if os.getenv("OPENAI_SSL", "0") == "0" else False


print(f"====== database type:      {Cfg.db_type} ======")
print(f"====== data base dir:      {Cfg.base_dir} ======")
print(f"====== scenarios root dir: {Cfg.scenarios_root} ======")
print(f"====== openai ssl: {Cfg.openai_ssl} ======")

__g_ca_file: tempfile.NamedTemporaryFile = None
__g_async_client: AsyncClient = None
__g_sync_client: OpenAI = None
__g_model_name: str = os.getenv("MODEL_NAME", "qwen-plus")


def get_ca_file(openai_base_url: str) -> tempfile.NamedTemporaryFile:
    global __g_ca_file
    
    hostname = urlparse(Cfg.openai_base_url).hostname
    context = SSL.Context(SSL.TLSv1_2_METHOD)
    conn = SSL.Connection(context, socket.socket())
    conn.connect((hostname, 443))
    conn.do_handshake()
    certs = conn.get_peer_cert_chain()
    conn.close()

    # Write certs to temp file
    __g_ca_file = tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False)
    for cert in certs:
        __g_ca_file.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode())
    __g_ca_file.close()

    return __g_ca_file

def get_async_client() -> AsyncClient:
    global __g_async_client, __g_ca_file
    if __g_async_client is not None:
        return __g_async_client

    openai_base_url = os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    if Cfg.openai_ssl:
        if __g_ca_file is None:
            __g_ca_file = get_ca_file(openai_base_url)

        __g_async_client = AsyncClient(
            api_key = os.getenv("OPENAI_API_KEY", "sk-placeholder"),
            base_url = openai_base_url,
            http_client=httpx.AsyncClient(verify=__g_ca_file.name)
        )
    else:
        __g_async_client = AsyncClient(
            api_key = os.getenv("OPENAI_API_KEY", "sk-placeholder"),
            base_url = openai_base_url,
        )
    return __g_async_client


def get_sync_client() -> OpenAI:
    global __g_sync_client, __g_ca_file
    if __g_async_client is not None:
        return __g_async_client

    openai_base_url = os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    if Cfg.openai_ssl:
        if __g_ca_file is None:
            __g_ca_file = get_ca_file(openai_base_url)

        __g_sync_client = OpenAI(
            api_key = os.getenv("OPENAI_API_KEY", "sk-placeholder"),
            base_url = openai_base_url,
            http_client=httpx.Client(verify=__g_ca_file.name)
        )
    else:
        __g_sync_client = OpenAI(
            api_key = os.getenv("OPENAI_API_KEY", "sk-placeholder"),
            base_url = openai_base_url,
        )
    return __g_sync_client
    

def get_model_name():
    global __g_model_name
    return __g_model_name
