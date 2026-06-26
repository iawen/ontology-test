import asyncio
import os
import socket
import tempfile
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from openai import AsyncClient, OpenAI
from OpenSSL import SSL, crypto

load_dotenv()

__g_asycn_client: AsyncClient = None
__g_async_client_loop: asyncio.AbstractEventLoop | None = None
__g_sync_client: OpenAI = None
__g_model_name: str = os.getenv("MODEL_NAME", "qwen-plus")


def _build_ca_file() -> str:
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    hostname = urlparse(base_url).hostname
    context = SSL.Context(SSL.TLSv1_2_METHOD)
    conn = SSL.Connection(context, socket.socket())
    conn.connect((hostname, 443))
    conn.do_handshake()
    certs = conn.get_peer_cert_chain()
    conn.close()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as ca_file:
        for cert in certs:
            ca_file.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode())
        return ca_file.name


def get_async_client() -> AsyncClient:
    global __g_asycn_client, __g_async_client_loop

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if __g_asycn_client is not None and (
        current_loop is None or (__g_async_client_loop is current_loop and not current_loop.is_closed())
    ):
        return __g_asycn_client

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    ca_file = _build_ca_file()
    __g_asycn_client = AsyncClient(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=base_url,
        http_client=httpx.AsyncClient(verify=ca_file),
    )
    __g_async_client_loop = current_loop
    return __g_asycn_client


async def get_async_client_async() -> AsyncClient:
    global __g_asycn_client, __g_async_client_loop

    current_loop = asyncio.get_running_loop()
    if __g_asycn_client is not None and __g_async_client_loop is current_loop and not current_loop.is_closed():
        return __g_asycn_client

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    ca_file = await asyncio.to_thread(_build_ca_file)
    __g_asycn_client = AsyncClient(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=base_url,
        http_client=httpx.AsyncClient(verify=ca_file),
    )
    __g_async_client_loop = current_loop
    return __g_asycn_client


def get_sync_client() -> OpenAI:
    global __g_sync_client
    if __g_sync_client is not None:
        return __g_sync_client

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    ca_file = _build_ca_file()
    __g_sync_client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=base_url,
        http_client=httpx.Client(verify=ca_file),
    )
    return __g_sync_client


def get_model_name() -> str:
    global __g_model_name
    return __g_model_name
