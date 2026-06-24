
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

# from langchain_openai import ChatOpenAI

class Cfg:
    base_dir = os.path.join(str(Path(__file__).resolve().parent.parent), "data")
    
    db_type = os.getenv("DB_TYPE", "sqlite3")
    db_dsn = os.getenv("DATABASE_URL", "sqlite:///" + os.path.join(base_dir, "admin.db"))
    db_path = os.path.join(base_dir, "admin.db")

    jwt_secret = os.getenv("JWT_SECRET", "on-budget-ai-admin-2026")
    scenarios_root = os.path.join(base_dir, "scenarios")

    model_name = os.getenv("MODEL_NAME", "qwen-plus")
    openai_api_key = os.getenv("OPENAI_API_KEY", "sk-placeholder")
    openai_base_url = os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")


print(f"====== database type:      {Cfg.db_type} ======")
print(f"====== data base dir:      {Cfg.base_dir} ======")
print(f"====== scenarios root dir: {Cfg.scenarios_root} ======")

client = AsyncClient(
    api_key = Cfg.openai_api_key,
    base_url = Cfg.openai_base_url,
)


hostname = urlparse(Cfg.openai_base_url).hostname
context = SSL.Context(SSL.TLSv1_2_METHOD)
conn = SSL.Connection(context, socket.socket())
conn.connect((hostname, 443))
conn.do_handshake()
certs = conn.get_peer_cert_chain()
conn.close()

# Write certs to temp file
ca_file = tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False)
for cert in certs:
    ca_file.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode())
ca_file.close()


client = AsyncClient(
    api_key = Cfg.openai_api_key,
    base_url = Cfg.openai_base_url,
    http_client=httpx.AsyncClient(verify=ca_file.name)
)

client2 = OpenAI(
    api_key = Cfg.openai_api_key,
    base_url = Cfg.openai_base_url,
    http_client=httpx.Client(verify=ca_file.name)
)